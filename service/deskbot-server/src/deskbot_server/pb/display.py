"""表情 LCD 逻辑分辨率（与固件 / pb 下发坐标一致）。"""

from __future__ import annotations

import copy
from typing import Any

FACE_LCD_WIDTH = 284
FACE_LCD_HEIGHT = 240

# 旧单色小屏（设计稿 / 迁移源坐标系）
FACE_LCD_LEGACY_WIDTH = 128
FACE_LCD_LEGACY_HEIGHT = 64

# 等比缩放，避免宽高分别拉伸导致表情变形
_SCALE_UNIFORM = min(
    FACE_LCD_WIDTH / FACE_LCD_LEGACY_WIDTH,
    FACE_LCD_HEIGHT / FACE_LCD_LEGACY_HEIGHT,
)
_OFFSET_X = int(round((FACE_LCD_WIDTH - FACE_LCD_LEGACY_WIDTH * _SCALE_UNIFORM) / 2))
_OFFSET_Y = int(round((FACE_LCD_HEIGHT - FACE_LCD_LEGACY_HEIGHT * _SCALE_UNIFORM) / 2))

_X_KEYS = frozenset({"x", "x0", "x1", "x2"})
_Y_KEYS = frozenset({"y", "y0", "y1", "y2"})
_SIZE_KEYS = frozenset({"w", "h", "r", "radius", "rw", "rh"})


def _scale_legacy_scalar(v: int | float) -> int:
    return int(round(float(v) * _SCALE_UNIFORM))


def _map_legacy_x(v: int | float) -> int:
    return _scale_legacy_scalar(v) + _OFFSET_X


def _map_legacy_y(v: int | float) -> int:
    return _scale_legacy_scalar(v) + _OFFSET_Y


def scale_coord_x(v: int | float) -> int:
    return _map_legacy_x(v)


def scale_coord_y(v: int | float) -> int:
    return _map_legacy_y(v)


def scale_size_w(v: int | float) -> int:
    return _scale_legacy_scalar(v)


def scale_size_h(v: int | float) -> int:
    return _scale_legacy_scalar(v)


def scale_radius(v: int | float) -> int:
    return max(1, _scale_legacy_scalar(v))


def scale_primitive(prim: dict[str, Any]) -> dict[str, Any]:
    """将 128×64 坐标系下的单图元等比缩放到当前 LCD（居中留边）。"""
    out = copy.deepcopy(prim)
    for k, val in list(out.items()):
        if not isinstance(val, (int, float)):
            continue
        if k in _X_KEYS:
            out[k] = _map_legacy_x(val)
        elif k in _Y_KEYS:
            out[k] = _map_legacy_y(val)
        elif k in _SIZE_KEYS:
            out[k] = _scale_legacy_scalar(val)
    return out


def scale_primitives(prims: list[Any]) -> list[dict[str, Any]]:
    return [
        scale_primitive(p)
        for p in prims
        if isinstance(p, dict) and str(p.get("shape") or "").strip()
    ]


def scale_offset(off: dict[str, Any] | None) -> dict[str, int]:
    """口型组 ``offset`` 为相对位移，仅按等比系数缩放。"""
    if not isinstance(off, dict):
        return {"x": 0, "y": 0}
    return {
        "x": _scale_legacy_scalar(off.get("x") or 0),
        "y": _scale_legacy_scalar(off.get("y") or 0),
    }


def scale_anim_elements(elements: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, val in elements.items():
        if isinstance(val, list):
            out[key] = scale_primitives(val)
        else:
            out[key] = val
    return out


def scale_mouth_group_entry(entry: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(entry)
    if isinstance(out.get("elements"), list):
        out["elements"] = scale_primitives(out["elements"])
    if "offset" in out:
        out["offset"] = scale_offset(out.get("offset"))
    return out


def scale_expr_scene(scene: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(scene)
    frames = out.get("frames")
    if not isinstance(frames, list):
        return out
    for fr in frames:
        if not isinstance(fr, dict):
            continue
        els = fr.get("elements")
        if isinstance(els, dict):
            fr["elements"] = scale_anim_elements(els)
    return out


def scale_face_document(doc: Any) -> Any:
    """从 128×64 文档迁移到当前 LCD 分辨率。"""
    if isinstance(doc, list):
        scaled: list[Any] = []
        for item in doc:
            if not isinstance(item, dict):
                scaled.append(item)
                continue
            if "frames" in item:
                scaled.append(scale_expr_scene(item))
            elif "elements" in item or "states" in item:
                scaled.append(scale_mouth_group_entry(item))
            else:
                scaled.append(item)
        return scaled
    if isinstance(doc, dict):
        if "frames" in doc:
            return scale_expr_scene(doc)
        if "elements" in doc or "states" in doc:
            return scale_mouth_group_entry(doc)
    return doc
