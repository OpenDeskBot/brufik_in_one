"""LiteLLM 运行时：解析设备/系统 LLM 配置并发起 completion。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

from deskbot_server.config import load_config
from deskbot_server.llm_config_store import LlmModelEntry, get_active_llm_model


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


def litellm_completion(
    messages: list[dict[str, str]],
    *,
    device_id: Optional[str] = None,
    temperature: float = 0.7,
    config: ResolvedLlmConfig | None = None,
    json_mode: bool = True,
) -> tuple[str, dict[str, Any]]:
    """调用 LiteLLM completion，返回 (content, meta)。"""
    try:
        import litellm
    except ImportError as exc:
        raise RuntimeError("litellm 未安装，请执行 pip install litellm") from exc

    cfg = config or resolve_llm_config(device_id)
    if not cfg.api_key or "请替换" in cfg.api_key:
        raise ValueError(
            "LLM API Key 未配置。请在设备 LLM 管理中设置，或通过环境变量 LLM_API_KEY / DASHSCOPE_API_KEY 传入。"
        )

    kwargs: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "temperature": temperature,
        "api_key": cfg.api_key,
    }
    if cfg.api_base:
        kwargs["api_base"] = cfg.api_base.rstrip("/")
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = litellm.completion(**kwargs)
    content = ""
    try:
        content = (response.choices[0].message.content or "").strip()
    except (AttributeError, IndexError, TypeError):
        content = ""

    usage_dict: dict[str, Any] | None = None
    usage = getattr(response, "usage", None)
    if usage is not None:
        try:
            usage_dict = {
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
            }
        except Exception:
            usage_dict = None

    meta = {
        "model": cfg.model,
        "source": cfg.source,
        "display_name": cfg.display_name,
        "usage": usage_dict,
    }
    return content, meta
