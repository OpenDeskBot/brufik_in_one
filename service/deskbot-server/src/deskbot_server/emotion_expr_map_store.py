from __future__ import annotations

import json
import os
from typing import Optional

from deskbot_server.device_data import resolve_json_path

EMOTION_EXPR_MAP_FILE = "emotion_expr_map.json"


def _normalize(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ValueError("emotion map must be an object")
    out: dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(v, str):
            raise ValueError(f"scene for emotion {k!r} must be a string")
        out[str(k)] = v
    return out


def load_emotion_expr_map(*, device_id: Optional[str] = None) -> dict[str, str]:
    path = resolve_json_path(EMOTION_EXPR_MAP_FILE, device_id)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return _normalize(json.load(f))


def save_emotion_expr_map(
    mapping: dict[str, str], *, device_id: Optional[str] = None
) -> dict[str, str]:
    norm = _normalize(mapping)
    path = resolve_json_path(EMOTION_EXPR_MAP_FILE, device_id)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(norm, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return norm
