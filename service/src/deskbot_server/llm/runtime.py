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

VOLCENGINE_PROTOCOLS = {"ark", "volcengine", "doubao"}
OPENAI_COMPAT_PROTOCOLS = {
    "openai",
    "ark",
    "volcengine",
    "doubao",
    "dashscope",
    "qwen",
}
LEGACY_MODEL_PREFIXES = OPENAI_COMPAT_PROTOCOLS | {"azure", "anthropic", "gemini", "ollama"}


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
    if p == "volcano":
        return "volcengine"
    return p


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
        or ("ark" if api_key_source in {"ARK_API_KEY", "VOLCENGINE_API_KEY", "DOUBAO_API_KEY"} else None)
        or llm_cfg.get("protocol")
        or "openai"
    )
    model_name = str(
        os.environ.get("LLM_MODEL")
        or os.environ.get("ARK_MODEL")
        or os.environ.get("VOLCENGINE_LLM_MODEL")
        or llm_cfg.get("model_name")
        or "qwen-flash"
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
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


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
    payload: dict[str, Any] = {
        "model": build_chat_model(cfg.protocol, cfg.model),
        "messages": messages,
        "temperature": temperature,
        "stream": stream,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    return payload


def _usage_from_response(response: Any) -> dict[str, Any] | None:
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


def _content_from_response(response: Any) -> str:
    if isinstance(response, dict):
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
) -> tuple[str, dict[str, Any]]:
    """Call an OpenAI-compatible Chat Completions endpoint."""
    cfg = config or resolve_llm_config(device_id)
    use_stream = False
    if stream or on_tts_ready:
        logger.debug("[LLM] direct runtime currently uses non-stream request and post-response TTS hook")
    response = await asyncio.to_thread(
        _request_chat_completion,
        messages,
        cfg,
        temperature=temperature,
        json_mode=json_mode,
        stream=use_stream,
    )
    content = _content_from_response(response)
    usage_dict = _usage_from_response(response)

    if on_tts_ready is not None:
        text = JsonTtsStreamExtractor().feed(content)
        if text:
            result = on_tts_ready(text)
            if inspect.isawaitable(result):
                await result

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
