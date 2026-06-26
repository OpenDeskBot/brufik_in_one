"""LiteLLM 运行时：解析设备/系统 LLM 配置并发起 completion。"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Optional

from deskbot_server.config import load_config
from deskbot_server.llm.stream_tts import JsonTtsStreamExtractor
from deskbot_server.llm_config_store import LlmModelEntry, get_active_llm_model

logger = logging.getLogger("deskbot-server")


@dataclass(frozen=True)
class ResolvedLlmConfig:
    model: str
    api_key: str
    api_base: str | None
    protocol: str
    source: str  # "device" | "system"
    display_name: str


def resolve_system_llm_config() -> ResolvedLlmConfig:
    cfg = load_config()
    llm_cfg = dict(cfg.get("llm") or {})
    model_name = str(llm_cfg.get("model_name") or "qwen-flash").strip()
    base_url = str(llm_cfg.get("base_url") or "").strip() or None
    api_key = (
        os.environ.get("LLM_API_KEY")
        or os.environ.get("DASHSCOPE_API_KEY")
        or os.environ.get("QWEN_API_KEY")
        or ""
    ).strip()
    return ResolvedLlmConfig(
        model=build_litellm_model("openai", model_name),
        api_key=api_key,
        api_base=base_url,
        protocol="openai",
        source="system",
        display_name=f"系统默认 ({model_name})",
    )


def resolve_llm_config(device_id: Optional[str] = None) -> ResolvedLlmConfig:
    entry = get_active_llm_model(device_id)
    if entry is None:
        return resolve_system_llm_config()
    return _entry_to_config(entry)


def _entry_to_config(entry: LlmModelEntry) -> ResolvedLlmConfig:
    api_base = str(entry.base_url or "").strip() or None
    return ResolvedLlmConfig(
        model=build_litellm_model(entry.protocol, entry.model_name),
        api_key=str(entry.api_key or "").strip(),
        api_base=api_base,
        protocol=entry.protocol,
        source="device",
        display_name=entry.name,
    )


def build_litellm_model(protocol: str, model_name: str) -> str:
    protocol = str(protocol or "openai").strip().lower() or "openai"
    model_name = str(model_name or "").strip()
    if not model_name:
        raise ValueError("model_name required")
    if "/" in model_name:
        return model_name
    if protocol == "openai":
        return f"openai/{model_name}"
    return f"{protocol}/{model_name}"


def _build_completion_kwargs(
    messages: list[dict[str, str]],
    cfg: ResolvedLlmConfig,
    *,
    temperature: float,
    json_mode: bool,
    stream: bool,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "temperature": temperature,
        "api_key": cfg.api_key,
        "stream": stream,
    }
    if cfg.api_base:
        kwargs["api_base"] = cfg.api_base.rstrip("/")
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    return kwargs


def _usage_from_response(response: Any) -> dict[str, Any] | None:
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


def _content_from_response(response: Any) -> str:
    try:
        return (response.choices[0].message.content or "").strip()
    except (AttributeError, IndexError, TypeError):
        return ""


def _delta_from_stream_chunk(chunk: Any) -> str:
    try:
        choice = chunk.choices[0]
        delta = getattr(choice, "delta", None)
        if delta is not None:
            return str(getattr(delta, "content", None) or "")
        message = getattr(choice, "message", None)
        if message is not None:
            return str(getattr(message, "content", None) or "")
    except (AttributeError, IndexError, TypeError):
        pass
    return ""


async def litellm_acompletion(
    messages: list[dict[str, str]],
    *,
    device_id: Optional[str] = None,
    temperature: float = 0.7,
    config: ResolvedLlmConfig | None = None,
    json_mode: bool = True,
    stream: bool = False,
    on_tts_ready: Optional[Callable[[str], Awaitable[None]]] = None,
) -> tuple[str, dict[str, Any]]:
    """异步调用 LiteLLM completion，返回 (content, meta)。

    ``stream=True`` 且提供 ``on_tts_ready`` 时，会在 JSON 流中 ``tts`` 字段闭合后
    尽早回调，无需等待整段响应结束。
    """
    try:
        import litellm
    except ImportError as exc:
        raise RuntimeError("litellm 未安装，请执行 pip install litellm") from exc

    cfg = config or resolve_llm_config(device_id)
    if not cfg.api_key or "请替换" in cfg.api_key:
        raise ValueError(
            "LLM API Key 未配置。请在设备 LLM 管理中设置，或通过环境变量 LLM_API_KEY / DASHSCOPE_API_KEY 传入。"
        )

    use_stream = bool(stream or on_tts_ready)
    kwargs = _build_completion_kwargs(
        messages,
        cfg,
        temperature=temperature,
        json_mode=json_mode,
        stream=use_stream,
    )

    content = ""
    usage_dict: dict[str, Any] | None = None

    if use_stream:
        tts_extractor: JsonTtsStreamExtractor | None = None
        tts_notified = False

        async def _notify_tts(text: str) -> None:
            nonlocal tts_notified
            if tts_notified or not on_tts_ready:
                return
            tts_notified = True
            await on_tts_ready(text)

        if on_tts_ready is not None:

            def _sync_hook(text: str) -> None:
                asyncio.get_running_loop().create_task(_notify_tts(text))

            tts_extractor = JsonTtsStreamExtractor(on_tts_ready=_sync_hook)

        response = await litellm.acompletion(**kwargs)
        parts: list[str] = []
        last_chunk: Any = None
        async for chunk in response:
            last_chunk = chunk
            delta = _delta_from_stream_chunk(chunk)
            if delta:
                parts.append(delta)
                if tts_extractor is not None:
                    tts_extractor.feed(delta)
        content = "".join(parts).strip()
        if last_chunk is not None:
            usage_dict = _usage_from_response(last_chunk)
    else:
        response = await litellm.acompletion(**kwargs)
        content = _content_from_response(response)
        usage_dict = _usage_from_response(response)

    meta = {
        "model": cfg.model,
        "source": cfg.source,
        "display_name": cfg.display_name,
        "usage": usage_dict,
    }
    return content, meta


def litellm_completion(
    messages: list[dict[str, str]],
    *,
    device_id: Optional[str] = None,
    temperature: float = 0.7,
    config: ResolvedLlmConfig | None = None,
    json_mode: bool = True,
) -> tuple[str, dict[str, Any]]:
    """同步包装，供 Flask 调试接口等同步上下文使用。"""
    return asyncio.run(
        litellm_acompletion(
            messages,
            device_id=device_id,
            temperature=temperature,
            config=config,
            json_mode=json_mode,
            stream=False,
        )
    )
