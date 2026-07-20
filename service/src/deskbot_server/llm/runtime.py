"""OpenAI-compatible LLM runtime.

The default direct provider for China deployments is Volcengine Ark:
``https://ark.cn-beijing.volces.com/api/v3``.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Optional

from deskbot_server.config import load_config
from deskbot_server.llm.stream_tts import JsonTtsStreamExtractor
from deskbot_server.llm_config_store import LlmModelEntry, get_active_llm_model

logger = logging.getLogger("deskbot-server")

ARK_OPENAI_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
OPENAI_BASE_URL = "https://api.openai.com/v1"
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_TIMEOUT_SECONDS = 60
LLM_FIRST_TOKEN_TIMEOUT_SECONDS = 5.0

VOLCENGINE_PROTOCOLS = {"ark", "ark_responses", "volcengine", "doubao"}
OPENAI_COMPAT_PROTOCOLS = {
    "openai",
    "ark",
    "volcengine",
    "doubao",
    "dashscope",
    "qwen",
}
ARK_RESPONSES_PROTOCOLS = {"ark_responses"}
LEGACY_MODEL_PREFIXES = (
    OPENAI_COMPAT_PROTOCOLS | ARK_RESPONSES_PROTOCOLS | {"azure", "anthropic", "gemini", "ollama"}
)


@dataclass(frozen=True)
class ResolvedLlmConfig:
    model: str
    api_key: str
    api_base: str | None
    protocol: str
    source: str  # "device" | "system" | "test"
    display_name: str


def _first_env(*names: str) -> tuple[str, str | None]:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip(), name
    return "", None


def _normalized_protocol(protocol: str | None) -> str:
    p = str(protocol or "openai").strip().lower() or "openai"
    if p == "byteark":
        return "ark"
    if p in {"ark-responses", "arkresponses"}:
        return "ark_responses"
    if p == "volcano":
        return "volcengine"
    return p


def _uses_ark_responses_api(protocol: str) -> bool:
    return _normalized_protocol(protocol) in ARK_RESPONSES_PROTOCOLS


def resolve_first_token_timeout(protocol: str) -> float:
    raw = str(os.environ.get("LLM_FIRST_TOKEN_TIMEOUT") or "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    # ark_responses 在联网/工具阶段可能长时间无 output_text.delta；首字超时误杀，交给总超时。
    if _uses_ark_responses_api(protocol):
        return 0.0
    return LLM_FIRST_TOKEN_TIMEOUT_SECONDS


def _default_base_url(protocol: str, *, api_key_source: str | None = None) -> str | None:
    protocol = _normalized_protocol(protocol)
    if protocol in VOLCENGINE_PROTOCOLS:
        return ARK_OPENAI_BASE_URL
    if protocol == "dashscope" or api_key_source in {"DASHSCOPE_API_KEY", "QWEN_API_KEY"}:
        return DASHSCOPE_BASE_URL
    if protocol == "openai" and api_key_source in {"ARK_API_KEY", "VOLCENGINE_API_KEY", "DOUBAO_API_KEY"}:
        return ARK_OPENAI_BASE_URL
    if protocol == "openai":
        return OPENAI_BASE_URL
    return None


def _resolve_api_base(
    protocol: str,
    configured_base_url: str | None,
    *,
    api_key_source: str | None = None,
) -> str | None:
    base_url = str(configured_base_url or "").strip()
    if base_url:
        return base_url.rstrip("/")
    default_base = _default_base_url(protocol, api_key_source=api_key_source)
    return default_base.rstrip("/") if default_base else None


def resolve_system_llm_config() -> ResolvedLlmConfig:
    cfg = load_config()
    llm_cfg = dict(cfg.get("llm") or {})
    api_key, api_key_source = _first_env(
        "LLM_API_KEY",
        "ARK_API_KEY",
        "VOLCENGINE_API_KEY",
        "DOUBAO_API_KEY",
        "DASHSCOPE_API_KEY",
        "QWEN_API_KEY",
    )
    protocol = _normalized_protocol(
        os.environ.get("LLM_PROTOCOL")
        or llm_cfg.get("protocol")
        or ("ark" if api_key_source in {"ARK_API_KEY", "VOLCENGINE_API_KEY", "DOUBAO_API_KEY"} else None)
        or "openai"
    )
    model_name = str(
        os.environ.get("LLM_MODEL")
        or os.environ.get("ARK_MODEL")
        or os.environ.get("VOLCENGINE_LLM_MODEL")
        or llm_cfg.get("model_name")
        or ""
    ).strip()
    base_url = _first_env(
        "LLM_BASE_URL",
        "ARK_BASE_URL",
        "VOLCENGINE_LLM_BASE_URL",
        "VOLCENGINE_API_BASE",
        "DOUBAO_LLM_BASE_URL",
        "DASHSCOPE_BASE_URL",
    )[0] or str(llm_cfg.get("base_url") or "").strip()
    resolved_base = _resolve_api_base(protocol, base_url, api_key_source=api_key_source)
    return ResolvedLlmConfig(
        model=build_chat_model(protocol, model_name),
        api_key=api_key,
        api_base=resolved_base,
        protocol=protocol,
        source="system",
        display_name=f"系统默认 ({model_name})",
    )


def resolve_llm_config(device_id: Optional[str] = None) -> ResolvedLlmConfig:
    entry = get_active_llm_model(device_id)
    if entry is None:
        return resolve_system_llm_config()
    return _entry_to_config(entry)


def _entry_to_config(entry: LlmModelEntry) -> ResolvedLlmConfig:
    protocol = _normalized_protocol(entry.protocol)
    api_base = _resolve_api_base(protocol, entry.base_url)
    return ResolvedLlmConfig(
        model=build_chat_model(protocol, entry.model_name),
        api_key=str(entry.api_key or "").strip(),
        api_base=api_base,
        protocol=protocol,
        source="device",
        display_name=entry.name,
    )


def build_chat_model(protocol: str, model_name: str) -> str:
    """Return the raw model/endpoint ID used by OpenAI-compatible APIs.

    Older code prefixed models for the previous adapter (for example ``openai/foo``).  The
    direct HTTP API expects the provider model ID only, so known compatibility
    prefixes are stripped while real ``org/model`` IDs are preserved.
    """
    _ = _normalized_protocol(protocol)
    raw_model = str(model_name or "").strip()
    if not raw_model:
        raise ValueError("model_name required")
    if "/" not in raw_model:
        return raw_model
    prefix, rest = raw_model.split("/", 1)
    if prefix.strip().lower() in LEGACY_MODEL_PREFIXES and rest.strip():
        return rest.strip()
    return raw_model


def _completion_url(api_base: str | None, protocol: str) -> str:
    base = _resolve_api_base(protocol, api_base)
    if not base:
        raise ValueError("LLM Base URL 未配置。请填写 Base URL 或选择火山方舟/Ark 协议。")
    base = base.rstrip("/")
    if _uses_ark_responses_api(protocol):
        if base.endswith("/responses"):
            return base
        return f"{base}/responses"
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _messages_to_ark_input(messages: list[dict[str, str]]) -> list[dict[str, Any]]:
    """OpenAI ``messages`` → 火山方舟 Responses API ``input``。"""
    out: list[dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "user").strip() or "user"
        content = msg.get("content")
        if isinstance(content, list):
            parts: list[dict[str, Any]] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type") or "").strip()
                if item_type in {"input_text", "output_text", "text"}:
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str) and text:
                        parts.append({"type": "input_text", "text": text})
                elif item_type == "input_image" and isinstance(item.get("image_url"), str):
                    parts.append({"type": "input_image", "image_url": item["image_url"]})
            if parts:
                out.append({"role": role, "content": parts})
            continue
        text = _stringify_content(content).strip()
        if not text:
            continue
        out.append(
            {
                "role": role,
                "content": [{"type": "input_text", "text": text}],
            }
        )
    if not out:
        raise ValueError("LLM 请求缺少有效 input")
    return out


def _validate_api_key(cfg: ResolvedLlmConfig) -> None:
    if not cfg.api_key or "请替换" in cfg.api_key:
        raise ValueError(
            "LLM API Key 未配置。请在设备 LLM 管理中设置，或通过环境变量 "
            "LLM_API_KEY / ARK_API_KEY / VOLCENGINE_API_KEY / DOUBAO_API_KEY / "
            "DASHSCOPE_API_KEY 传入。"
        )


def _build_completion_payload(
    messages: list[dict[str, str]],
    cfg: ResolvedLlmConfig,
    *,
    temperature: float,
    json_mode: bool,
    stream: bool,
) -> dict[str, Any]:
    if _uses_ark_responses_api(cfg.protocol):
        payload: dict[str, Any] = {
            "model": build_chat_model(cfg.protocol, cfg.model),
            "input": _messages_to_ark_input(messages),
            "stream": stream,
            "thinking": {"type": "disabled"},
        }
        if json_mode:
            payload["text"] = {"format": {"type": "json_object"}}
        return payload

    payload = {
        "model": build_chat_model(cfg.protocol, cfg.model),
        "messages": messages,
        "temperature": temperature,
        "stream": stream,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    return payload


def _usage_from_response(response: Any, *, protocol: str = "openai") -> dict[str, Any] | None:
    if isinstance(response, dict) and _uses_ark_responses_api(protocol):
        return _usage_from_ark_response(response)

    if isinstance(response, dict):
        usage = response.get("usage")
        if not isinstance(usage, dict):
            return None
        return {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        }

    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    try:
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }
    except Exception:
        return None


def _stringify_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
                elif isinstance(item.get("content"), str):
                    parts.append(str(item["content"]))
        return "".join(parts)
    return "" if content is None else str(content)


def _content_from_response(response: Any, *, protocol: str = "openai") -> str:
    if isinstance(response, dict):
        if _uses_ark_responses_api(protocol):
            return _content_from_ark_response(response)
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0]
        if not isinstance(first, dict):
            return ""
        message = first.get("message")
        if isinstance(message, dict):
            return _stringify_content(message.get("content")).strip()
        delta = first.get("delta")
        if isinstance(delta, dict):
            return _stringify_content(delta.get("content")).strip()
        return ""

    try:
        return (response.choices[0].message.content or "").strip()
    except (AttributeError, IndexError, TypeError):
        return ""


def _content_from_ark_response(response: dict[str, Any]) -> str:
    output = response.get("output")
    if not isinstance(output, list):
        return ""
    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if str(block.get("type") or "") == "output_text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
    return "".join(parts).strip()


def _usage_from_ark_response(response: dict[str, Any]) -> dict[str, Any] | None:
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return None
    return {
        "prompt_tokens": usage.get("input_tokens"),
        "completion_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }


def _delta_content_from_sse_event(data: dict[str, Any], *, protocol: str = "openai") -> str:
    if _uses_ark_responses_api(protocol):
        event_type = str(data.get("type") or "")
        if event_type == "response.output_text.delta":
            delta = data.get("delta")
            return delta if isinstance(delta, str) else ""
        return ""

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    delta = first.get("delta")
    if isinstance(delta, dict):
        return _stringify_content(delta.get("content"))
    message = first.get("message")
    if isinstance(message, dict):
        return _stringify_content(message.get("content"))
    return ""


def _iter_sse_json_events(resp) -> Any:
    """从 OpenAI-compatible SSE 响应中逐条解析 ``data: {...}``。"""
    buf = b""
    while True:
        chunk = resp.read(4096)
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.strip()
            if not line or not line.startswith(b"data:"):
                continue
            payload = line[5:].strip()
            if payload == b"[DONE]":
                return
            try:
                data = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                yield data


def _request_chat_completion_stream(
    messages: list[dict[str, str]],
    cfg: ResolvedLlmConfig,
    *,
    temperature: float,
    json_mode: bool,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    on_delta: Optional[Callable[[str], None]] = None,
) -> tuple[str, Optional[dict[str, Any]]]:
    """SSE 流式 Chat Completions；``on_delta`` 收到 content 增量时回调。"""
    _validate_api_key(cfg)
    url = _completion_url(cfg.api_base, cfg.protocol)
    payload = _build_completion_payload(
        messages,
        cfg,
        temperature=temperature,
        json_mode=json_mode,
        stream=True,
    )
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": "deskbot-server/0.1",
        },
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", "replace").strip()
        preview = err_body[:1000] if err_body else str(exc)
        raise RuntimeError(f"LLM API 请求失败 HTTP {exc.code}: {preview}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM API 请求失败: {exc.reason}") from exc

    parts: list[str] = []
    usage: Optional[dict[str, Any]] = None
    try:
        with resp:
            for event in _iter_sse_json_events(resp):
                piece = _delta_content_from_sse_event(event, protocol=cfg.protocol)
                if piece:
                    parts.append(piece)
                    if on_delta is not None:
                        on_delta(piece)
                if _uses_ark_responses_api(cfg.protocol):
                    if str(event.get("type") or "") == "response.completed":
                        response_obj = event.get("response")
                        if isinstance(response_obj, dict):
                            usage = _usage_from_ark_response(response_obj)
                    continue
                event_usage = event.get("usage")
                if isinstance(event_usage, dict):
                    usage = {
                        "prompt_tokens": event_usage.get("prompt_tokens"),
                        "completion_tokens": event_usage.get("completion_tokens"),
                        "total_tokens": event_usage.get("total_tokens"),
                    }
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", "replace").strip()
        preview = err_body[:1000] if err_body else str(exc)
        raise RuntimeError(f"LLM SSE 读取失败 HTTP {exc.code}: {preview}") from exc

    return "".join(parts).strip(), usage


def _request_chat_completion(
    messages: list[dict[str, str]],
    cfg: ResolvedLlmConfig,
    *,
    temperature: float,
    json_mode: bool,
    stream: bool,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    _validate_api_key(cfg)
    url = _completion_url(cfg.api_base, cfg.protocol)
    payload = _build_completion_payload(
        messages,
        cfg,
        temperature=temperature,
        json_mode=json_mode,
        stream=stream,
    )
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "deskbot-server/0.1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", "replace").strip()
        preview = err_body[:1000] if err_body else str(exc)
        raise RuntimeError(f"LLM API 请求失败 HTTP {exc.code}: {preview}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM API 请求失败: {exc.reason}") from exc

    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        preview = raw[:1000].decode("utf-8", "replace")
        raise RuntimeError(f"LLM API 返回不是合法 JSON: {preview}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("LLM API 返回格式异常：顶层不是 JSON object")
    return data


async def chat_acompletion(
    messages: list[dict[str, str]],
    *,
    device_id: Optional[str] = None,
    temperature: float = 0.7,
    config: ResolvedLlmConfig | None = None,
    json_mode: bool = True,
    stream: bool = False,
    on_tts_ready: Optional[Callable[[str], Awaitable[None]]] = None,
    first_token_timeout: float | None = None,
) -> tuple[str, dict[str, Any]]:
    """Call an OpenAI-compatible Chat Completions endpoint."""
    cfg = config or resolve_llm_config(device_id)
    if first_token_timeout is None:
        first_token_timeout = resolve_first_token_timeout(cfg.protocol)
    use_stream = bool(stream or on_tts_ready)
    usage_dict: Optional[dict[str, Any]] = None
    tts_extractor: JsonTtsStreamExtractor | None = None

    async def _fire_tts_ready(text: str) -> None:
        if not on_tts_ready or not text:
            return
        result = on_tts_ready(text)
        if inspect.isawaitable(result):
            await result

    if use_stream:
        tts_extractor = JsonTtsStreamExtractor()
        pending_tts: dict[str, Optional[str]] = {"text": None}
        loop = asyncio.get_running_loop()
        first_token_event = asyncio.Event()

        def _on_delta(piece: str) -> None:
            if tts_extractor is None:
                return
            if piece and not first_token_event.is_set():
                loop.call_soon_threadsafe(first_token_event.set)
            ready = tts_extractor.feed(piece)
            if ready:
                pending_tts["text"] = ready

        stream_task = asyncio.create_task(
            asyncio.to_thread(
                _request_chat_completion_stream,
                messages,
                cfg,
                temperature=temperature,
                json_mode=json_mode,
                on_delta=_on_delta,
            )
        )

        # 首字超时检测：first_token_timeout 秒内若无任何 delta 则放弃
        if first_token_timeout > 0:
            token_wait_task = asyncio.create_task(first_token_event.wait())
            done, _ = await asyncio.wait(
                {token_wait_task, stream_task},
                timeout=first_token_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            # 清理 token 等待 task
            if not token_wait_task.done():
                token_wait_task.cancel()
                try:
                    await token_wait_task
                except asyncio.CancelledError:
                    pass
            # 若超时且 stream 仍未完成、且未收到首字 → 放弃
            if stream_task not in done and not first_token_event.is_set():
                stream_task.cancel()
                try:
                    await stream_task
                except (asyncio.CancelledError, Exception):
                    pass
                raise TimeoutError(
                    f"LLM 首字超时（{first_token_timeout:.0f}s 内无内容返回）"
                )
            # stream 已完成（可能是错误），但未收到首字 → 让 await stream_task 正常抛出错误
            # stream 仍在运行但已收到首字 → 正常继续等待

        content, usage_dict = await stream_task

        if pending_tts["text"]:
            await _fire_tts_ready(pending_tts["text"])
        elif tts_extractor is not None and not tts_extractor._fired:
            ready = tts_extractor.feed("")
            if ready:
                await _fire_tts_ready(ready)
        logger.debug(
            "[LLM] SSE 流式完成 device_id=%s chars=%d tts_prefetch=%s",
            device_id,
            len(content),
            bool(tts_extractor and tts_extractor._fired),
        )
    else:
        response = await asyncio.to_thread(
            _request_chat_completion,
            messages,
            cfg,
            temperature=temperature,
            json_mode=json_mode,
            stream=False,
        )
        content = _content_from_response(response, protocol=cfg.protocol)
        usage_dict = _usage_from_response(response, protocol=cfg.protocol)

    meta = {
        "model": build_chat_model(cfg.protocol, cfg.model),
        "source": cfg.source,
        "display_name": cfg.display_name,
        "usage": usage_dict,
    }
    return content, meta


def chat_completion(
    messages: list[dict[str, str]],
    *,
    device_id: Optional[str] = None,
    temperature: float = 0.7,
    config: ResolvedLlmConfig | None = None,
    json_mode: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Synchronous wrapper for Flask endpoints."""
    return asyncio.run(
        chat_acompletion(
            messages,
            device_id=device_id,
            temperature=temperature,
            config=config,
            json_mode=json_mode,
            stream=False,
        )
    )
