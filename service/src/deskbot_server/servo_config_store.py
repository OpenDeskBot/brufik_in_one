"""舵机调试配置持久化（``data/servo.json``）。"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from deskbot_server.constants import SERVO_CFG_FILE
from deskbot_server.device_data import resolve_json_path

DEFAULT_SERVO_LIMITS: dict[str, int] = {
    "xMin": 0,
    "xMax": 180,
    "yMin": 70,
    "yMax": 110,
    "xReverse": 0,
    "yReverse": 0,
}

DEFAULT_SERVO_PERSPECTIVE = "viewer"
_VALID_PERSPECTIVES = frozenset({"viewer", "robot"})

# 观众视角下 left/right 类预设 id 与本体存储 id 的对调（预设 steps 仍为本体逻辑坐标）
VIEWER_LR_SWAP: dict[str, str] = {
    "look_left": "look_right",
    "look_right": "look_left",
    "look_upper_left": "look_upper_right",
    "look_upper_right": "look_upper_left",
    "look_lower_left": "look_lower_right",
    "look_lower_right": "look_lower_left",
}


def normalize_perspective(raw: object) -> str:
    p = str(raw or DEFAULT_SERVO_PERSPECTIVE).strip().lower()
    return p if p in _VALID_PERSPECTIVES else DEFAULT_SERVO_PERSPECTIVE


def servo_perspective(*, device_id: Optional[str] = None) -> str:
    """``servo.json`` 中的 left/right 语义：``viewer``（默认）或 ``robot``。"""
    try:
        cfg = load_servo_cfg_file(device_id=device_id)
    except (OSError, ValueError):
        cfg = None
    if cfg:
        return normalize_perspective(cfg.get("perspective"))
    return DEFAULT_SERVO_PERSPECTIVE


def resolve_move_for_perspective(
    move_id: str,
    *,
    device_id: Optional[str] = None,
    perspective: Optional[str] = None,
) -> str:
    """按视角解析 move/preset id（``viewer`` 时对调 left/right 类预设）。"""
    pid = str(move_id or "").strip()
    if not pid:
        return pid
    pers = normalize_perspective(perspective) if perspective is not None else servo_perspective(
        device_id=device_id
    )
    if pers != "viewer":
        return pid
    want = pid.lower()
    for src, dst in VIEWER_LR_SWAP.items():
        if src.lower() == want:
            return dst
    return pid


def _clamp_axis(v: int, lo: int, hi: int) -> int:
    a = min(int(lo), int(hi))
    b = max(int(lo), int(hi))
    return max(a, min(b, int(v)))


def _limits_with_reverse(
    limits: Optional[dict[str, int]] = None, *, device_id: Optional[str] = None
) -> dict[str, int]:
    lim = dict(DEFAULT_SERVO_LIMITS)
    if limits:
        lim.update({k: int(limits[k]) for k in DEFAULT_SERVO_LIMITS if k in limits})
    else:
        lim.update(servo_limits(device_id=device_id))
    return lim


def logical_step_to_protocol(
    step: dict[str, Any],
    limits: dict[str, int],
) -> dict[str, int]:
    """逻辑坐标 step → PB 协议坐标（与调试页 ``_servoStepToProtocol`` 一致）。"""
    xm = 1 if int(step.get("xm", 0)) == 1 else 0
    ym = 1 if int(step.get("ym", 0)) == 1 else 0
    lx = int(step.get("x", 0))
    ly = int(step.get("y", 0))
    ms = int(step.get("ms", 0))
    x_rev = int(limits.get("xReverse", 0)) == 1
    y_rev = int(limits.get("yReverse", 0)) == 1

    if xm == 0:
        clx = _clamp_axis(lx, limits["xMin"], limits["xMax"])
        x = (limits["xMin"] + limits["xMax"] - clx) if x_rev else clx
    else:
        dx = lx
        if x_rev:
            dx = -dx
        x = int(dx)

    if ym == 0:
        cly = _clamp_axis(ly, limits["yMin"], limits["yMax"])
        y = (limits["yMin"] + limits["yMax"] - cly) if y_rev else cly
    else:
        dy = ly
        if y_rev:
            dy = -dy
        y = int(dy)

    return {"xm": xm, "ym": ym, "x": x, "y": y, "ms": ms}


def servo_limits(*, device_id: Optional[str] = None) -> dict[str, int]:
    """读取设备/全局 ``servo.json`` 限位；缺失字段回退 ``DEFAULT_SERVO_LIMITS``。"""
    out = dict(DEFAULT_SERVO_LIMITS)
    try:
        cfg = load_servo_cfg_file(device_id=device_id)
    except (OSError, ValueError):
        cfg = None
    if cfg:
        for key in out:
            if key in cfg:
                out[key] = int(cfg[key])
    return out


def clamp_servo_step(
    step: dict[str, Any],
    *,
    device_id: Optional[str] = None,
    limits: Optional[dict[str, int]] = None,
) -> dict[str, int]:
    """逻辑 step → 协议 step（限位 clamp + xReverse/yReverse）。"""
    lim = _limits_with_reverse(limits, device_id=device_id)
    return logical_step_to_protocol(step, lim)


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
    out["perspective"] = normalize_perspective(raw.get("perspective"))
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
    norm = normalize_servo_document(cfg, require_presets="presets" in cfg)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(norm, f, ensure_ascii=False, indent=2)
        f.write("\n")
