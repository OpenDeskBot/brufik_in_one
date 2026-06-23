"""读写 deskbot-server/.env 中的豆包 TTS 配置。"""

from __future__ import annotations

import os
import re
from typing import Iterable

from deskbot_server.env import load_dotenv
from deskbot_server.paths import ENV_FILE
from deskbot_server.tts.doubao import _is_masked_secret

DOUBAO_TTS_ENV_KEYS = (
    "DOUBAO_TTS_API_KEY",
    "DOUBAO_TTS_SPEAKER",
    "DOUBAO_TTS_RESOURCE_ID",
    "DOUBAO_TTS_MODEL",
    "DOUBAO_TTS_WS_URL",
    "DOUBAO_TTS_SAMPLE_RATE",
    "DOUBAO_TTS_FORMAT",
)

_PAYLOAD_FIELD_BY_ENV_KEY = {
    "DOUBAO_TTS_API_KEY": "api_key",
    "DOUBAO_TTS_SPEAKER": "speaker",
    "DOUBAO_TTS_RESOURCE_ID": "resource_id",
    "DOUBAO_TTS_MODEL": "model",
    "DOUBAO_TTS_WS_URL": "ws_url",
    "DOUBAO_TTS_SAMPLE_RATE": "sample_rate",
    "DOUBAO_TTS_FORMAT": "audio_format",
}


def _quote_env_value(value: str) -> str:
    raw = value or ""
    if not raw:
        return ""
    if re.search(r'[\s#="\']', raw):
        escaped = raw.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return raw


def read_env_file() -> dict[str, str]:
    out: dict[str, str] = {}
    if not ENV_FILE.is_file():
        return out
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        body = stripped[7:].strip() if stripped.startswith("export ") else stripped
        if "=" not in body:
            continue
        key, val = body.split("=", 1)
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        out[key] = val
    return out


def update_env_keys(updates: dict[str, str], *, keys: Iterable[str] | None = None) -> None:
    """更新 .env 中指定键，保留其它行与注释。值为空时不覆盖已有行。"""
    allowed = set(keys or DOUBAO_TTS_ENV_KEYS)
    filtered = {k: (updates.get(k) or "").strip() for k in allowed if k in updates}
    if not filtered:
        return

    lines: list[str] = []
    if ENV_FILE.is_file():
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()

    new_lines: list[str] = []
    seen: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        body = stripped[7:].strip() if stripped.startswith("export ") else stripped
        if "=" not in body:
            new_lines.append(line)
            continue
        key = body.split("=", 1)[0].strip()
        if key in filtered:
            val = filtered[key]
            if val:
                new_lines.append(f"{key}={_quote_env_value(val)}")
            else:
                new_lines.append(line)
            seen.add(key)
        else:
            new_lines.append(line)

    missing = [k for k in filtered if k not in seen and filtered[k]]
    if missing:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append("# 豆包语音 TTS 2.0")
        for key in missing:
            new_lines.append(f"{key}={_quote_env_value(filtered[key])}")

    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENV_FILE.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")

    for key, val in filtered.items():
        if val:
            os.environ[key] = val


def save_doubao_tts_env(payload: dict[str, str]) -> None:
    """保存豆包 TTS 配置到 .env 并刷新进程内环境变量。留空字段不覆盖已有值。"""
    existing = read_env_file()
    updates: dict[str, str] = {}
    for env_key in DOUBAO_TTS_ENV_KEYS:
        payload_key = _PAYLOAD_FIELD_BY_ENV_KEY[env_key]
        raw = str(payload.get(payload_key) or "").strip()
        if env_key == "DOUBAO_TTS_API_KEY" and _is_masked_secret(raw):
            raw = ""
        if not raw:
            raw = (existing.get(env_key) or "").strip()
        updates[env_key] = raw
    update_env_keys(updates)
    load_dotenv()
