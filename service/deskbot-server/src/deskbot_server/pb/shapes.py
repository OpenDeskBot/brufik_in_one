"""图元 shape、口型/眼鼻展开与默认嘴型。"""

from __future__ import annotations

import copy
import json
import logging
import re
from typing import Any

from deskbot_server.pb.display import FACE_LCD_HEIGHT, FACE_LCD_WIDTH, scale_primitive

_logger = logging.getLogger(__name__)

# pb 下行 ``action`` / ``level``（见 ``docs/esp32_pb_protocol.md``）
PB_ACTION_REPLACE = "replace"
PB_ACTION_APPEND = "append"
PB_ACTION_DEFAULT = "default"

PB_LEVEL_IDLE = 0
PB_LEVEL_TASK = 1
PB_LEVEL_EMERGENCY = 2
PB_LEVEL_DEBUG = 3

PB_DEFAULT_PRIMITIVE_COLOR = "#FFFFFF"
PB_DEFAULT_RGB565 = 0xFFFF
_HEX_COLOR_RE = re.compile(r"^[0-9A-Fa-f]{6}$")

# 配置源可写 ``c: "red"`` / ``color: "#F80"``；wire 统一下发 ``c``（RGB565 整数）
_NAMED_RGB888: dict[str, tuple[int, int, int]] = {
    "white": (255, 255, 255),
    "black": (0, 0, 0),
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
    "orange": (255, 136, 0),
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),
}


def apply_pb_dispatch_fields(
    frames: list[dict[str, Any]],
    *,
    action: str,
    level: int,
) -> None:
    """为 pb 链各分片写入统一的 ``action`` / ``level``。"""
    for one in frames:
        one["action"] = action
        one["level"] = int(level)


_PB_HINT_TYPES = frozenset({"pb_start", "pb_chunk", "pb_end", "pb_single"})


def attach_pb_device_hints(
    msg: dict[str, Any],
    *,
    volume: int | None = None,
    mic: str | None = None,
) -> None:
    """为 ``pb_start`` / ``pb_chunk`` / ``pb_end`` / ``pb_single`` 附加设备侧提示。"""
    if msg.get("type") not in _PB_HINT_TYPES:
        return
    if volume is not None:
        msg["volume"] = int(volume)
    if mic is not None and str(mic).strip().lower() in ("mute", "open"):
        msg["mic"] = str(mic).strip().lower()


def apply_pb_device_hints_to_frames(
    frames: list[dict[str, Any]],
    *,
    volume: int | None = None,
    mic: str | None = None,
) -> None:
    """为 pb 链各分片写入统一的 ``volume`` / ``mic``（仅适用类型）。"""
    for one in frames:
        attach_pb_device_hints(one, volume=volume, mic=mic)


_PAUSE_PHONEME_ALIASES = frozenset({"sil", "sp", "spl", "spn", "sp1", "sp2", "sp3", "sp4"})


def simplify_phoneme_key(ph: str) -> str:
    """口型查表键：中文去末尾声调 1–5；英文 ARPAbet 去末尾重音 0/1；停顿归 ``_``。"""
    p = str(ph or "").strip()
    if not p or p == "_":
        return "_"
    low = p.lower()
    if low in _PAUSE_PHONEME_ALIASES:
        return "_"
    if len(p) >= 2 and p[-1] in "12345" and p[-2].isalpha():
        return p[:-1]
    if p.isascii() and p.isalpha() and len(p) >= 2 and p[-1] in "01":
        return p[:-1]
    return p


def enumerate_zh_phonemes() -> list[str]:
    finals = [
        "a",
        "o",
        "e",
        "i",
        "u",
        "v",
        "ai",
        "ei",
        "ao",
        "ou",
        "an",
        "en",
        "ang",
        "eng",
        "ong",
        "er",
        "ia",
        "ie",
        "iao",
        "iu",
        "ian",
        "in",
        "iang",
        "ing",
        "iong",
        "ua",
        "uo",
        "uai",
        "ui",
        "uan",
        "un",
        "uang",
        "ve",
        "iou",
        "uei",
        "uen",
    ]
    initials = [
        "b",
        "p",
        "m",
        "f",
        "d",
        "t",
        "n",
        "l",
        "g",
        "k",
        "h",
        "zh",
        "ch",
        "sh",
        "r",
        "z",
        "c",
        "s",
        "j",
        "q",
        "x",
        "y",
        "w",
    ]
    s: set[str] = {"_", "sil"}
    for f in finals:
        s.add(f)
    for ini in initials:
        s.add(ini)

    def sort_key(a: str) -> tuple[int, str]:
        if a == "_":
            return (-1, "")
        return (0, a)

    return sorted(s, key=sort_key)


def _legacy_default_mouth_rect_for_phoneme(ph: str) -> list[dict[str, Any]]:
    """128×64 模板；经 ``scale_primitive`` 映射到当前 LCD。"""
    ph = simplify_phoneme_key(ph)
    cx = 64
    y0 = 44
    closed_h = 4
    open_h = 15
    mid_h = 9
    if ph in ("_", "sil"):
        w, h = 30, closed_h
        raw = [{"shape": "rect", "x": round(cx - w / 2), "y": 52, "w": w, "h": h}]
    elif ph in (
        "a", "o", "e", "ai", "ei", "ao", "ou", "er", "ua", "uo", "uai", "uei",
    ):
        w, h = 40, open_h
        raw = [{"shape": "rect", "x": round(cx - w / 2), "y": y0, "w": w, "h": h}]
    elif ph.startswith(("i", "u", "v")) or ph in ("iu", "ui", "ve"):
        w, h = 26, mid_h
        raw = [{"shape": "rect", "x": round(cx - w / 2), "y": y0, "w": w, "h": h}]
    elif len(ph) <= 2:
        w, h = 14, mid_h
        raw = [{"shape": "rect", "x": round(cx - w / 2), "y": y0, "w": w, "h": h}]
    else:
        w, h = 32, mid_h
        raw = [{"shape": "rect", "x": round(cx - w / 2), "y": y0, "w": w, "h": h}]
    return [scale_primitive(p) for p in raw]


def default_mouth_rect_for_phoneme(ph: str) -> list[dict[str, Any]]:
    return _legacy_default_mouth_rect_for_phoneme(ph)


def default_mouth_by_phoneme() -> dict[str, list[dict[str, Any]]]:
    return {p: default_mouth_rect_for_phoneme(p) for p in enumerate_zh_phonemes()}


def default_face_circles() -> dict[str, list[dict[str, Any]]]:
    legacy = {
        "nose": [{"shape": "circle", "x": 64, "y": 34, "r": 5}],
        "eye_l": [{"shape": "circle", "x": 42, "y": 26, "r": 7}],
        "eye_r": [{"shape": "circle", "x": 86, "y": 26, "r": 7}],
    }
    return {k: [scale_primitive(p) for p in v] for k, v in legacy.items()}


def _default_mouth_fallback_shape() -> dict[str, Any]:
    return {
        "elements": [
            scale_primitive({"shape": "rect", "x": 46, "y": 46, "w": 36, "h": 9}),
        ],
        "offset": {"x": 0, "y": 0},
    }


# shape 主名与别名（小写键）→ 主名；与 docs/esp32_pb_protocol.md 一致
_SHAPE_TO_CANONICAL: dict[str, str] = {
    "fill_rect": "rect",
    "fillrect": "rect",
    "draw_rect": "rect_outline",
    "drawrect": "rect_outline",
    "fill_circle": "circle",
    "fillcircle": "circle",
    "draw_circle": "circle_outline",
    "drawcircle": "circle_outline",
    "point": "pixel",
    "drawpixel": "pixel",
    "h_line": "hline",
    "drawfasthline": "hline",
    "v_line": "vline",
    "drawfastvline": "vline",
    "draw_ellipse": "ellipse",
    "drawellipse": "ellipse",
    "fill_ellipse": "ellipse_fill",
    "fillellipse": "ellipse_fill",
    "draw_triangle": "triangle",
    "drawtriangle": "triangle",
    "fill_triangle": "triangle_fill",
    "filltriangle": "triangle_fill",
    "fill_round_rect": "round_rect",
    "fillroundrect": "round_rect",
    "draw_round_rect": "round_rect_outline",
    "drawroundrect": "round_rect_outline",
    "draw_rotated_rect": "rotated_rect_outline",
    "drawrotatedrect": "rotated_rect_outline",
    "fill_rotated_rect": "rotated_rect_fill",
    "fillrotatedrect": "rotated_rect_fill",
    "drawline": "line",
}


def normalize_primitive_shape(shape: str) -> str:
    """将 ``shape`` 别名归一为协议主名（小写）。"""
    s = str(shape or "").strip().lower()
    return _SHAPE_TO_CANONICAL.get(s, s)


def rgb888_to_rgb565(r: int, g: int, b: int) -> int:
    """8-bit RGB → RGB565（16 位无符号整数）。"""
    r5 = (int(r) >> 3) & 0x1F
    g6 = (int(g) >> 2) & 0x3F
    b5 = (int(b) >> 3) & 0x1F
    return (r5 << 11) | (g6 << 5) | b5


def parse_color_to_rgb888(raw: Any) -> tuple[int, int, int] | None:
    """解析配置侧颜色：``#RGB`` / ``#RRGGBB`` / 命名色 / RGB888 整数。"""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        v = int(raw) & 0xFFFFFF
        return ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)
    s = str(raw).strip()
    if not s:
        return None
    low = s.lower()
    if low in _NAMED_RGB888:
        return _NAMED_RGB888[low]
    if s.startswith("#"):
        h = s[1:]
    elif _HEX_COLOR_RE.match(s):
        h = s
    else:
        return None
    if len(h) == 3 and all(c in "0123456789abcdefABCDEF" for c in h):
        h = "".join(ch * 2 for ch in h)
    if _HEX_COLOR_RE.match(h):
        v = int(h, 16)
        return ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)
    return None


def parse_color_to_rgb565(raw: Any, *, default: int = PB_DEFAULT_RGB565) -> int:
    """解析为 RGB565；无效或空则 ``default``（默认白 ``0xFFFF``）。"""
    if raw is None:
        return int(default) & 0xFFFF
    if isinstance(raw, bool):
        return int(default) & 0xFFFF
    if isinstance(raw, int):
        v = int(raw)
        if 0 <= v <= 0xFFFF:
            return v
        return rgb888_to_rgb565((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)
    rgb = parse_color_to_rgb888(raw)
    if rgb is None:
        return int(default) & 0xFFFF
    return rgb888_to_rgb565(*rgb)


def normalize_primitive_color(raw: Any) -> int:
    """兼容旧测试名：等价 ``parse_color_to_rgb565``。"""
    return parse_color_to_rgb565(raw)


def normalize_primitive_for_wire(prim: dict[str, Any]) -> dict[str, Any]:
    """下发前规范化单图元：``c`` 为 RGB565 整数；移除 ``color``。"""
    out = copy.deepcopy(prim)
    raw = out.get("c")
    if raw is None:
        raw = out.get("color")
    out.pop("color", None)
    out["c"] = parse_color_to_rgb565(raw)
    return out


def apply_anim_bg_color_elements(
    elements: dict[str, Any],
    *,
    bg: Any = None,
    color: Any = None,
) -> dict[str, Any]:
    """将 LLM ``anims[]`` 项上的 ``bg`` / ``color`` 并入 ``elements``（配置侧字符串，wire 前转 ``c``）。"""
    out = copy.deepcopy(elements)
    if bg is not None and str(bg).strip() != "":
        out["bg"] = [
            {
                "shape": "rect",
                "x": 0,
                "y": 0,
                "w": FACE_LCD_WIDTH,
                "h": FACE_LCD_HEIGHT,
                "color": bg,
            }
        ]
    if color is not None and str(color).strip() != "":
        for layer in ("extra", "mouth", "nose", "eye_l", "eye_r", "bg"):
            prims = out.get(layer)
            if not isinstance(prims, list):
                continue
            for prim in prims:
                if not isinstance(prim, dict):
                    continue
                if str(prim.get("shape") or "").strip().lower() == "image":
                    continue
                if prim.get("c") is None and prim.get("color") is None:
                    prim["color"] = color
    return out


def normalize_elements_for_wire(elements: dict[str, Any]) -> dict[str, Any]:
    """规范化 ``anim.elements`` 各层图元列表。"""
    out: dict[str, Any] = {}
    for key, val in (elements or {}).items():
        if isinstance(val, list):
            out[key] = [
                normalize_primitive_for_wire(p)
                for p in val
                if isinstance(p, dict) and str(p.get("shape") or "").strip()
            ]
        else:
            out[key] = copy.deepcopy(val)
    return out


def normalize_anim_item_for_wire(item: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(item)
    els = out.get("elements")
    if isinstance(els, dict):
        out["elements"] = normalize_elements_for_wire(els)
    return out


def normalize_anim_list_for_wire(anim: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [normalize_anim_item_for_wire(x) for x in anim if isinstance(x, dict)]


def _add_offset_to_primitive_inplace(q: dict[str, Any], dx: int, dy: int) -> None:
    """按协议平移图元坐标（``q`` 已为可变副本）。"""
    if dx == 0 and dy == 0:
        return
    sh = normalize_primitive_shape(str(q.get("shape") or ""))
    if sh in ("rect", "rect_outline", "round_rect", "round_rect_outline"):
        q["x"] = int(q.get("x", 0)) + dx
        q["y"] = int(q.get("y", 0)) + dy
    elif sh in ("circle", "circle_outline"):
        q["x"] = int(q.get("x", 0)) + dx
        q["y"] = int(q.get("y", 0)) + dy
    elif sh == "line":
        q["x1"] = int(q.get("x1", 0)) + dx
        q["y1"] = int(q.get("y1", 0)) + dy
        q["x2"] = int(q.get("x2", 0)) + dx
        q["y2"] = int(q.get("y2", 0)) + dy
    elif sh == "pixel":
        q["x"] = int(q.get("x", 0)) + dx
        q["y"] = int(q.get("y", 0)) + dy
    elif sh in ("hline", "vline"):
        q["x"] = int(q.get("x", 0)) + dx
        q["y"] = int(q.get("y", 0)) + dy
    elif sh in ("ellipse", "ellipse_fill"):
        q["x"] = int(q.get("x", 0)) + dx
        q["y"] = int(q.get("y", 0)) + dy
    elif sh in ("triangle", "triangle_fill"):
        if "x0" in q or "y0" in q:
            if "x0" in q:
                q["x0"] = int(q.get("x0", 0)) + dx
            if "y0" in q:
                q["y0"] = int(q.get("y0", 0)) + dy
        elif "x" in q and "y" in q:
            q["x"] = int(q.get("x", 0)) + dx
            q["y"] = int(q.get("y", 0)) + dy
        if "x1" in q:
            q["x1"] = int(q.get("x1", 0)) + dx
        if "y1" in q:
            q["y1"] = int(q.get("y1", 0)) + dy
        if "x2" in q:
            q["x2"] = int(q.get("x2", 0)) + dx
        if "y2" in q:
            q["y2"] = int(q.get("y2", 0)) + dy
    elif sh in ("rotated_rect_outline", "rotated_rect_fill"):
        q["x"] = int(q.get("x", 0)) + dx
        q["y"] = int(q.get("y", 0)) + dy


def apply_offset_to_primitives(
    primitives: list[dict[str, Any]], dx: int, dy: int
) -> list[dict[str, Any]]:
    """对眼、鼻等图元做平移（嘴不调）；支持协议主名及别名（见 ``normalize_primitive_shape``）。"""
    if dx == 0 and dy == 0:
        return copy.deepcopy(primitives)
    out: list[dict[str, Any]] = []
    for p in primitives or []:
        q = copy.deepcopy(p)
        _add_offset_to_primitive_inplace(q, dx, dy)
        out.append(q)
    return out


def _normalize_offset(raw: Any) -> tuple[int, int]:
    if not isinstance(raw, dict):
        return (0, 0)
    try:
        return (int(raw.get("x", 0)), int(raw.get("y", 0)))
    except (TypeError, ValueError):
        return (0, 0)


def _normalize_mouth_entry(raw: Any) -> dict[str, Any]:
    """``{ "elements": [...], "offset": {x,y} }``；缺字段时补齐（忽略 ``states`` 音素列表键）。"""
    if isinstance(raw, list):
        return {"elements": copy.deepcopy(raw), "offset": {"x": 0, "y": 0}}
    if not isinstance(raw, dict):
        return copy.deepcopy(_default_mouth_fallback_shape())
    els = raw.get("elements")
    if not isinstance(els, list):
        els = []
    off = _normalize_offset(raw.get("offset"))
    return {"elements": copy.deepcopy(els), "offset": {"x": off[0], "y": off[1]}}


def is_mouth_phoneme_group_entry(val: Any) -> bool:
    """口型共享条（仅用于 ``mouth_by_phoneme_groups`` 数组项）：``states`` + ``elements`` + ``offset``。"""
    if not isinstance(val, dict):
        return False
    st = val.get("states")
    if not isinstance(st, list) or not st:
        return False
    if not all(isinstance(x, str) and str(x).strip() for x in st):
        return False
    if not isinstance(val.get("elements"), list):
        return False
    return True


def expand_mouth_by_phoneme(
    mouth_by: dict[str, Any] | None,
    groups: list[Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """把口型配置展开为 音素 -> ``{elements, offset}``，供查表。

    - ``mouth_by_phoneme_groups``：共享条 **数组**，每项 ``{ "states", "elements", "offset" }``。
    - ``mouth_by_phoneme``：仅 **音素键** → ``{ "elements", "offset" }`` 或图元列表；**不得**再内嵌共享条。
    - 同一音素：先应用数组共享条，再应用 ``mouth_by_phoneme`` 单键（后者覆盖）。
    """
    out: dict[str, dict[str, Any]] = {}

    def _apply_group(entry: Any) -> None:
        if not is_mouth_phoneme_group_entry(entry):
            return
        norm = _normalize_mouth_entry(entry)
        for p in entry.get("states") or []:
            ph = str(p).strip()
            if ph:
                out[ph] = copy.deepcopy(norm)

    if isinstance(groups, list):
        for item in groups:
            _apply_group(item)

    if isinstance(mouth_by, dict):
        for k, v in mouth_by.items():
            if is_mouth_phoneme_group_entry(v):
                continue
            out[str(k)] = _normalize_mouth_entry(v)
    return out


def collapse_mouth_by_phoneme(mouth_flat: dict[str, Any]) -> dict[str, Any]:
    """把已展开的音素→口型表合并为 ``mouth_by_phoneme`` + 可选 ``mouth_by_phoneme_groups``。

    多音素同签名的条目写入 **数组** ``mouth_by_phoneme_groups``；单音素留在 ``mouth_by_phoneme``。
    """
    if not isinstance(mouth_flat, dict):
        return {"mouth_by_phoneme": {}}
    sig_to_phones: dict[str, list[str]] = {}
    sig_norm: dict[str, dict[str, Any]] = {}
    for ph, raw in mouth_flat.items():
        norm = _normalize_mouth_entry(raw)
        sig = json.dumps(
            [norm["elements"], norm["offset"]],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        sig_to_phones.setdefault(sig, []).append(str(ph))
        sig_norm[sig] = norm
    singles: dict[str, Any] = {}
    groups_out: list[dict[str, Any]] = []
    for sig in sorted(sig_to_phones.keys()):
        phones = sorted(set(sig_to_phones[sig]))
        norm = sig_norm[sig]
        if len(phones) == 1:
            singles[phones[0]] = {
                "elements": copy.deepcopy(norm["elements"]),
                "offset": dict(norm["offset"]),
            }
        else:
            groups_out.append(
                {
                    "states": phones,
                    "elements": copy.deepcopy(norm["elements"]),
                    "offset": dict(norm["offset"]),
                }
            )
    result: dict[str, Any] = {"mouth_by_phoneme": singles}
    if groups_out:
        result["mouth_by_phoneme_groups"] = groups_out
    return result


def _eye_primitives_list(raw: Any) -> list[dict[str, Any]]:
    return copy.deepcopy(raw) if isinstance(raw, list) else []


_EYE_ANIM_STATE_KEYS = frozenset({"default", "open", "close"})


def is_eye_elements_group_entry(val: Any) -> bool:
    """眼共享条：``{ "states": ["default","open",...], "elements": [图元...] }``。

    ``states`` 须为非空列表且每项为 ``default`` / ``open`` / ``close``；须含 ``elements`` 数组。
    与口的 ``mouth_by_phoneme_groups`` 条目区分（口含 ``offset`` 且 ``states`` 为音素）。
    """
    if not isinstance(val, dict):
        return False
    st = val.get("states")
    if not isinstance(st, list) or not st:
        return False
    if not all(isinstance(x, str) and x in _EYE_ANIM_STATE_KEYS for x in st):
        return False
    return isinstance(val.get("elements"), list)


def is_nose_elements_group_entry(val: Any) -> bool:
    """鼻共享条：``{ "states": ["default"], "elements": [图元...] }``（目前仅 ``default``）。"""
    if not isinstance(val, dict):
        return False
    st = val.get("states")
    if not isinstance(st, list) or not st:
        return False
    if not all(isinstance(x, str) and str(x) == "default" for x in st):
        return False
    return isinstance(val.get("elements"), list)


def is_extra_elements_group_entry(val: Any) -> bool:
    """附加层共享条（仅 ``extra_groups``）：``states`` + ``elements``，无 ``offset``。

    与口型组条区分：口型共享条只应出现在 ``mouth_by_phoneme_groups``（且通常含 ``offset``）；
    本函数仅在解析 ``extra_groups`` 时调用，故 **不要求** ``states`` 与眼三态互斥（可用 ``default`` 等任意态名表示情绪）。
    """
    if not isinstance(val, dict):
        return False
    if "offset" in val:
        return False
    st = val.get("states")
    if not isinstance(st, list) or not st:
        return False
    if not all(isinstance(x, str) and str(x).strip() for x in st):
        return False
    return isinstance(val.get("elements"), list)


def expand_extra_part(raw: Any, groups: list[Any] | None) -> dict[str, list[dict[str, Any]]]:
    """展开 ``extra``：``extra_groups`` 数组 + ``extra`` 对象上的各态图元列表（后者覆盖同态）。"""
    out: dict[str, list[dict[str, Any]]] = {}

    def _apply_group(entry: Any) -> None:
        if not is_extra_elements_group_entry(entry):
            return
        els = _eye_primitives_list(entry.get("elements"))
        for s in entry.get("states") or []:
            sk = str(s).strip()
            if sk:
                out[sk] = copy.deepcopy(els)

    if isinstance(groups, list):
        for item in groups:
            _apply_group(item)
    if isinstance(raw, dict):
        for k, v in raw.items():
            if str(k).startswith("_"):
                continue
            if isinstance(v, list):
                out[str(k)] = _eye_primitives_list(v)
    if "default" not in out:
        out["default"] = []
    return out


def expand_eye_part(
    raw: Any,
    groups: list[Any] | None,
) -> dict[str, list[dict[str, Any]]]:
    """展开 ``eye_*``：``*_groups`` 数组 + ``eye_*`` 对象上 ``default``/``open``/``close`` 图元列表（后者覆盖同态）。"""
    p = copy.deepcopy(raw) if isinstance(raw, dict) else {}
    out: dict[str, list[dict[str, Any]]] = {"default": [], "open": [], "close": []}

    def _apply_group(entry: Any) -> None:
        if not is_eye_elements_group_entry(entry):
            return
        els = _eye_primitives_list(entry.get("elements"))
        for s in entry.get("states") or []:
            if s in _EYE_ANIM_STATE_KEYS:
                out[str(s)] = copy.deepcopy(els)

    if isinstance(groups, list):
        for item in groups:
            _apply_group(item)
    for k in _EYE_ANIM_STATE_KEYS:
        if k in p and isinstance(p.get(k), list):
            out[k] = _eye_primitives_list(p[k])
    d, o, c = out["default"], out["open"], out["close"]
    if not o and d:
        o = copy.deepcopy(d)
    if not o and c:
        o = copy.deepcopy(c)
    if not c and d:
        c = copy.deepcopy(d)
    if not d and o:
        d = copy.deepcopy(o)
    return {"default": d, "open": o, "close": c}


def expand_nose_part(raw: Any, groups: list[Any] | None) -> dict[str, list[dict[str, Any]]]:
    """展开 ``nose``：``nose_groups`` + ``nose.default`` 图元列表（后者覆盖）。"""
    out: dict[str, list[dict[str, Any]]] = {"default": []}
    if isinstance(groups, list):
        for item in groups:
            if is_nose_elements_group_entry(item):
                out["default"] = copy.deepcopy(_eye_primitives_list(item.get("elements")))
    if not isinstance(raw, dict):
        return out
    p = copy.deepcopy(raw)
    if isinstance(p.get("default"), list):
        out["default"] = _eye_primitives_list(p["default"])
    return out


def collapse_eye_part(
    norm: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """导出眼图：统一写入 ``*_groups`` 数组（与鼻、口型组条排版一致）。

    - 图元序列相同的多个态合并为一条 ``states`` 多元素；否则每条 ``states`` 仅含一个态名。
    - 返回 ``({}, groups)``：``eye_l`` / ``eye_r`` 对象在导出文件中为空，由组条展开还原三态。
    """
    flat: dict[str, list[dict[str, Any]]] = {}
    order = ("default", "open", "close")
    for k in order:
        flat[k] = _eye_primitives_list(norm.get(k) if isinstance(norm, dict) else None)
    sig_to_states: dict[str, list[str]] = {}
    sig_prims: dict[str, list[dict[str, Any]]] = {}
    for sk, prims in flat.items():
        sig = json.dumps(prims, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        sig_to_states.setdefault(sig, []).append(sk)
        sig_prims[sig] = prims

    def _sort_states(states: list[str]) -> list[str]:
        return sorted(set(states), key=lambda s: order.index(s) if s in order else 99)

    tmp: list[tuple[int, str, dict[str, Any]]] = []
    for sig, sts_raw in sig_to_states.items():
        sts = _sort_states(sts_raw)
        pr = sig_prims[sig]
        prio = min(order.index(s) for s in sts) if sts else 99
        tmp.append((prio, sig, {"states": sts, "elements": copy.deepcopy(pr)}))
    tmp.sort(key=lambda x: (x[0], x[1]))
    groups_out = [t[2] for t in tmp]
    return {}, groups_out


def collapse_nose_part(
    norm: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """鼻仅 ``default``：导出为 ``nose: {}`` + ``nose_groups`` 单条（与 demo 口型排版一致）。"""
    d = _eye_primitives_list(norm.get("default") if isinstance(norm, dict) else None)
    if not d:
        return {}, []
    return {}, [{"states": ["default"], "elements": copy.deepcopy(d)}]


def _normalize_nose_part(part: Any) -> dict[str, list[dict[str, Any]]]:
    """鼻：仅 ``default``；无 ``nose_groups`` 时等价于 ``expand_nose_part(part, None)``。"""
    return expand_nose_part(part, None)


def _normalize_face_bundle_eyes_nose(fb: dict[str, Any]) -> None:
    """就地规范化眼、鼻：展开 ``*_groups`` 后与 ``eye_*`` / ``nose`` 合并，再删除组条键。"""
    for side in ("eye_l", "eye_r"):
        gk = f"{side}_groups"
        gl = fb.pop(gk, None)
        if not isinstance(gl, list):
            gl = None
        raw = fb.get(side)
        fb[side] = expand_eye_part(raw if isinstance(raw, dict) else {}, gl)
    ng = fb.pop("nose_groups", None)
    if not isinstance(ng, list):
        ng = None
    raw_n = fb.get("nose")
    fb["nose"] = expand_nose_part(raw_n, ng)


def _normalize_face_bundle_extra(fb: dict[str, Any]) -> None:
    """就地展开 ``extra_groups`` 进 ``extra`` 各态图元列表，再删除组条键。"""
    eg = fb.pop("extra_groups", None)
    gl = eg if isinstance(eg, list) else None
    raw = fb.get("extra")
    fb["extra"] = expand_extra_part(raw if isinstance(raw, dict) else {}, gl)


def _blink_eye_phase(elapsed_ms: int, blink_cfg: dict[str, Any]) -> str:
    """``"open"`` | ``"close"`` | ``"default"``（不眨眼时整段用 default）。"""
    try:
        open_ms = max(0, int(blink_cfg.get("open_ms", 3000)))
        close_ms = max(0, int(blink_cfg.get("close_ms", 100)))
    except (TypeError, ValueError):
        open_ms, close_ms = 3000, 100
    cycle = open_ms + close_ms
    if cycle <= 0:
        return "default"
    pos = max(0, elapsed_ms) % cycle
    return "open" if pos < open_ms else "close"

