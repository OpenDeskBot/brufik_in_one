"""舵机调试配置持久化（``data/servo.json``）。"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from deskbot_server.constants import SERVO_CFG_FILE
from deskbot_server.device_data import resolve_json_path


def normalize_servo_step(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        raise ValueError("preset step must be an object")
    out: dict[str, int] = {}
    for key in ("x", "y"):
        try:
            out[key] = max(-180, min(180, int(raw.get(key, 0))))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid step {key}") from exc
    for key in ("xm", "ym"):
        out[key] = 1 if int(raw.get(key, 0)) == 1 else 0
    try:
        out["ms"] = max(50, min(10000, int(raw.get("ms", 400))))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid step ms") from exc
    return out


def normalize_servo_preset(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("preset must be an object")
    preset_id = str(raw.get("id") or "").strip()
    label = str(raw.get("label") or "").strip()
    if not preset_id:
        raise ValueError("preset missing id")
    if not label:
        raise ValueError(f"preset {preset_id!r} missing label")
    steps_raw = raw.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise ValueError(f"preset {preset_id!r} requires non-empty steps")
    steps = [normalize_servo_step(s) for s in steps_raw]
    return {
        "id": preset_id,
        "label": label,
        "desc": str(raw.get("desc") or "").strip(),
        "steps": steps,
    }


def normalize_servo_document(raw: object, *, require_presets: bool = False) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("body must be a JSON object")
    out: dict[str, Any] = {}
    for key in ("xMin", "xMax", "yMin", "yMax"):
        if raw.get(key) is None:
            raise ValueError(f"missing {key}")
        try:
            val = int(raw[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid {key}") from exc
        out[key] = max(0, min(180, val))
    for key in ("xReverse", "yReverse"):
        if raw.get(key) is None:
            raise ValueError(f"missing {key}")
        out[key] = 1 if int(raw[key]) == 1 else 0
    x_min = min(out["xMin"], out["xMax"])
    x_max = max(out["xMin"], out["xMax"])
    y_min = min(out["yMin"], out["yMax"])
    y_max = max(out["yMin"], out["yMax"])
    out["xMin"], out["xMax"] = x_min, x_max
    out["yMin"], out["yMax"] = y_min, y_max
    if "presets" in raw or require_presets:
        presets_raw = raw.get("presets", [])
        if presets_raw is None:
            presets_raw = []
        if not isinstance(presets_raw, list):
            raise ValueError("presets must be an array")
        out["presets"] = [normalize_servo_preset(p) for p in presets_raw]
    return out


def load_servo_cfg_file(*, device_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    path = resolve_json_path(SERVO_CFG_FILE, device_id)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return normalize_servo_document(raw)


def save_servo_cfg_file(cfg: dict[str, Any], *, device_id: Optional[str] = None) -> None:
    path = resolve_json_path(SERVO_CFG_FILE, device_id)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
