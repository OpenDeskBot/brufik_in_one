"""读写 .env 中的大模型（LLM / 火山方舟 Ark）配置。

复用 tts.env_store 的通用 .env 读写（read_env_file / update_env_keys），
避免重复实现；写入后同步更新 os.environ，使运行中的进程立即生效。
"""

from __future__ import annotations

from typing import Any

from deskbot_server.tts.env_store import read_env_file, update_env_keys

# 密钥统一写 ARK_API_KEY：图片(ark_face_svg)与文本(resolve_system_llm_config)都会读它，
# 且写了 ARK_API_KEY 后协议会自动推断为 ark，无需用户再选。
LLM_ENV_KEYS = (
    "ARK_API_KEY",
    "LLM_PROTOCOL",
    "LLM_MODEL",
    "LLM_BASE_URL",
)

_ENV_KEY_BY_FIELD = {
    "api_key": "ARK_API_KEY",
    "protocol": "LLM_PROTOCOL",
    "model_name": "LLM_MODEL",
    "base_url": "LLM_BASE_URL",
}


def _looks_masked(value: str) -> bool:
    v = (value or "").strip()
    return not v or "*" in v or "•" in v or "…" in v


def save_llm_env(payload: dict[str, Any]) -> None:
    """把 payload 里的 LLM 字段写入 .env（并更新 os.environ）。

    api_key 为空或看起来是掩码时不覆盖已有值；其它字段按需写入。
    """
    updates: dict[str, str] = {}
    for field, env_key in _ENV_KEY_BY_FIELD.items():
        if field not in payload:
            continue
        val = str(payload.get(field) or "").strip()
        if env_key == "ARK_API_KEY" and _looks_masked(val):
            continue  # 保留已保存的 Key
        updates[env_key] = val
    if updates:
        update_env_keys(updates, keys=LLM_ENV_KEYS, comment="# 大模型 LLM / 火山方舟 Ark")


def read_llm_env() -> dict[str, str]:
    """读取 .env 中当前的 LLM 配置（原始值，供内部使用）。"""
    env = read_env_file()
    return {k: env.get(k, "") for k in LLM_ENV_KEYS}


def clear_llm_env() -> None:
    """从 .env 与进程环境变量中移除本机大模型配置（调试用：回到未配置状态）。"""
    import os

    from deskbot_server.paths import ENV_FILE

    if ENV_FILE.is_file():
        kept = []
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            body = line.strip()
            body = body[7:].strip() if body.startswith("export ") else body
            key = body.split("=", 1)[0].strip() if "=" in body else ""
            if key in LLM_ENV_KEYS:
                continue
            kept.append(line)
        ENV_FILE.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")
    for key in LLM_ENV_KEYS:
        os.environ.pop(key, None)
