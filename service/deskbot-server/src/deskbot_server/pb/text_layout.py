"""屏幕文案预排版（方案 A：服务端换行 → 多 ``text`` 图元）。"""

from __future__ import annotations

import re
from typing import Any

from deskbot_server.pb.display import FACE_LCD_HEIGHT, FACE_LCD_WIDTH

_DEFAULT_MARGIN_X = 8
_DEFAULT_MARGIN_Y = 8
_DEFAULT_SIZE = 1
_LINE_HEIGHT_MUL = 9


def _char_display_width(ch: str, *, size: int) -> int:
    if not ch:
        return 0
    if ord(ch) < 128:
        return max(4, 5 * size)
    return max(6, 8 * size)


def wrap_text_lines(
    text: str,
    *,
    max_width_px: int,
    size: int = _DEFAULT_SIZE,
) -> list[str]:
    """按像素宽度软换行；保留 ``\\n`` 硬断行。"""
    max_w = max(16, int(max_width_px))
    out: list[str] = []
    for para in str(text or "").split("\n"):
        if not para.strip():
            out.append("")
            continue
        line = ""
        line_w = 0
        for ch in para:
            cw = _char_display_width(ch, size=size)
            if line and line_w + cw > max_w:
                out.append(line)
                line = ch
                line_w = cw
            else:
                line += ch
                line_w += cw
        if line:
            out.append(line)
    return out or [""]


def text_primitives_from_block(
    text: str,
    *,
    x: int | None = None,
    y: int | None = None,
    color: Any = None,
    size: int = _DEFAULT_SIZE,
    max_width_px: int | None = None,
    max_height_px: int | None = None,
) -> list[dict[str, Any]]:
    """长文本 → ``extra`` 层多行 ``text`` 图元，避免超出 284×240。"""
    if not str(text or "").strip():
        return []
    margin_x = _DEFAULT_MARGIN_X if x is None else int(x)
    margin_y = _DEFAULT_MARGIN_Y if y is None else int(y)
    max_w = (
        FACE_LCD_WIDTH - margin_x * 2
        if max_width_px is None
        else int(max_width_px)
    )
    line_h = max(8, _LINE_HEIGHT_MUL * max(1, int(size)))
    max_h = FACE_LCD_HEIGHT - margin_y if max_height_px is None else int(max_height_px)
    max_lines = max(1, max_h // line_h)

    lines = wrap_text_lines(text, max_width_px=max_w, size=size)[:max_lines]
    prims: list[dict[str, Any]] = []
    for i, ln in enumerate(lines):
        if not ln:
            continue
        prim: dict[str, Any] = {
            "shape": "text",
            "x": margin_x,
            "y": margin_y + i * line_h,
            "text": ln,
            "size": max(1, int(size)),
        }
        if color is not None and str(color).strip():
            prim["color"] = color
        prims.append(prim)
    return prims
