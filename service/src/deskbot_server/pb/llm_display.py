"""LLM ``images`` → pb ``anim`` + ``assets``。"""

from __future__ import annotations

import base64
import io
import re
import binascii
from typing import Any

from deskbot_server.pb.display import FACE_LCD_HEIGHT, FACE_LCD_WIDTH

_B64_RE = re.compile(r"^data:image/[a-zA-Z0-9+.-]+;base64,", re.I)


def jpeg_blob_dimensions(jpeg_bytes: bytes) -> tuple[int, int]:
    """读取 JPEG 像素尺寸；失败时回退 LCD 逻辑分辨率。"""
    if not jpeg_bytes:
        return FACE_LCD_WIDTH, FACE_LCD_HEIGHT
    try:
        from PIL import Image  # type: ignore

        with Image.open(io.BytesIO(jpeg_bytes)) as im:
            w, h = im.size
            if w > 0 and h > 0:
                return int(w), int(h)
    except Exception:
        pass
    return FACE_LCD_WIDTH, FACE_LCD_HEIGHT


def resize_jpeg_bytes_to(jpeg_bytes: bytes, w: int, h: int) -> bytes:
    """将 JPEG 缩放到目标 (w,h) 再编码，使固件解码尺寸与 ``shape:image`` 一致。"""
    w = max(1, int(w))
    h = max(1, int(h))
    if not jpeg_bytes:
        return jpeg_bytes
    try:
        from PIL import Image  # type: ignore

        with Image.open(io.BytesIO(jpeg_bytes)) as im:
            if im.size == (w, h):
                return jpeg_bytes
            im = im.convert("RGB").resize((w, h), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            im.save(out, format="JPEG", quality=85)
            return out.getvalue()
    except Exception:
        return jpeg_bytes


def decode_llm_image_item(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    b64 = raw.get("b64") or raw.get("base64") or raw.get("data")
    if b64 is None:
        return None
    s = str(b64).strip()
    if not s:
        return None
    s = _B64_RE.sub("", s)
    try:
        data = base64.standard_b64decode(s)
    except (ValueError, TypeError, binascii.Error):
        return None
    if not data:
        return None
    try:
        x = int(raw.get("x", 0))
        y = int(raw.get("y", 0))
        w = int(raw.get("w", FACE_LCD_WIDTH))
        h = int(raw.get("h", FACE_LCD_HEIGHT))
    except (TypeError, ValueError):
        x, y, w, h = 0, 0, FACE_LCD_WIDTH, FACE_LCD_HEIGHT
    w = max(1, min(FACE_LCD_WIDTH, w))
    h = max(1, min(FACE_LCD_HEIGHT, h))
    x = max(0, min(FACE_LCD_WIDTH - 1, x))
    y = max(0, min(FACE_LCD_HEIGHT - 1, y))
    # 相机常为 320×240，屏为 284×240；下发前缩放到绘制区域，避免固件 stride 不一致花屏
    data = resize_jpeg_bytes_to(data, w, h)
    return {"bytes": data, "x": x, "y": y, "w": w, "h": h}


def parse_llm_images(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return out
    for item in raw:
        dec = decode_llm_image_item(item)
        if dec:
            out.append(dec)
    return out


def _attach_images_to_anim_item(
    item: dict[str, Any],
    images: list[dict[str, Any]],
    *,
    asset_base: int,
) -> None:
    if not images:
        return
    els = item.setdefault("elements", {})
    if not isinstance(els, dict):
        return
    layer = els.setdefault("extra", [])
    if not isinstance(layer, list):
        layer = []
        els["extra"] = layer
    for i, img in enumerate(images):
        layer.append(
            {
                "shape": "image",
                "asset": asset_base + i,
                "x": img["x"],
                "y": img["y"],
                "w": img["w"],
                "h": img["h"],
            }
        )


def apply_llm_display_to_rows(
    rows: list[dict[str, Any]],
    *,
    images: list[dict[str, Any]] | None = None,
) -> None:
    """将 LLM 图片叠加到 pb 行（就地修改）。"""
    if not rows:
        return
    imgs = list(images or [])
    asset_bytes = [img["bytes"] for img in imgs]

    for row in rows:
        anim = row.get("anim")
        if not isinstance(anim, list):
            continue
        for item in anim:
            if not isinstance(item, dict):
                continue
            _attach_images_to_anim_item(item, imgs, asset_base=0)

        if asset_bytes:
            row["_assets"] = list(asset_bytes)


def build_capture_image_for_display(jpeg_base64: str) -> dict[str, Any]:
    dec = decode_llm_image_item({"b64": jpeg_base64, "x": 0, "y": 0})
    if not dec:
        raise ValueError("invalid jpeg_base64")
    return dec
