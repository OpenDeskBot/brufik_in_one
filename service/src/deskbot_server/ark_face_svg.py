"""Ark Responses image-to-face SVG integration."""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Callable
from xml.etree import ElementTree as ET

from deskbot_server.face_expr_scenes_store import normalize_face_expr_scenes

ARK_RESPONSES_URL = "https://ark.cn-beijing.volces.com/api/v3/responses"
DEFAULT_IMAGE_MODEL = "doubao-seed-2-1-pro-260628"
DEFAULT_MAX_OUTPUT_TOKENS = 4096
MIN_ANIMATION_FRAMES = 4
MAX_IMAGE_BYTES = 6 * 1024 * 1024
ALLOWED_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
SCENE_LAYERS = ("eye_l", "eye_r", "nose", "mouth", "extra")
PB_SAFE_SCENE_SHAPES = {
    "ellipse",
    "ellipse_fill",
    "circle",
    "circle_outline",
    "rect",
    "rect_outline",
    "line",
    "round_rect",
    "round_rect_outline",
}

ArkTransport = Callable[[str, dict[str, Any], str, int], dict[str, Any]]

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)
_NAME_RE = re.compile(r"[^a-z0-9_]+", re.I)
_VALID_SCENE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$", re.I)
_SAFE_COLOR_RE = re.compile(
    r"^(none|currentColor|black|white|#[0-9a-fA-F]{3,8}|rgb\([0-9,\s.%-]+\)|rgba\([0-9,\s.%-]+\))$"
)
_ALLOWED_SVG_TAGS = {
    "svg",
    "g",
    "path",
    "circle",
    "ellipse",
    "rect",
    "line",
    "polyline",
    "polygon",
}
_ALLOWED_SVG_ATTRS = {
    "svg": {"viewBox", "width", "height", "fill", "stroke", "stroke-width"},
    "g": {"fill", "stroke", "stroke-width", "opacity", "transform"},
    "path": {
        "d",
        "fill",
        "stroke",
        "stroke-width",
        "stroke-linecap",
        "stroke-linejoin",
        "opacity",
        "fill-opacity",
        "stroke-opacity",
        "transform",
    },
    "circle": {"cx", "cy", "r", "fill", "stroke", "stroke-width", "opacity", "transform"},
    "ellipse": {"cx", "cy", "rx", "ry", "fill", "stroke", "stroke-width", "opacity", "transform"},
    "rect": {"x", "y", "width", "height", "rx", "ry", "fill", "stroke", "stroke-width", "opacity", "transform"},
    "line": {"x1", "y1", "x2", "y2", "stroke", "stroke-width", "stroke-linecap", "opacity", "transform"},
    "polyline": {"points", "fill", "stroke", "stroke-width", "stroke-linejoin", "opacity", "transform"},
    "polygon": {"points", "fill", "stroke", "stroke-width", "stroke-linejoin", "opacity", "transform"},
}


def _resolve_api_key(api_key: str | None = None) -> str:
    key = str(api_key or "").strip()
    if key:
        return key
    for name in ("ARK_API_KEY", "VOLCENGINE_API_KEY", "DOUBAO_API_KEY", "LLM_API_KEY"):
        key = str(os.environ.get(name) or "").strip()
        if key:
            return key
    raise ValueError("ARK_API_KEY 未配置，无法调用图片表情包生成。")


def _resolve_model(model: str | None = None) -> str:
    return (
        str(
            model
            or os.environ.get("ARK_IMAGE_TO_SVG_MODEL")
            or os.environ.get("ARK_VISION_MODEL")
            or DEFAULT_IMAGE_MODEL
        ).strip()
        or DEFAULT_IMAGE_MODEL
    )


def _resolve_max_output_tokens(value: int | None = None) -> int:
    raw = value if value is not None else os.environ.get("ARK_IMAGE_TO_SVG_MAX_OUTPUT_TOKENS")
    if raw is None or raw == "":
        return DEFAULT_MAX_OUTPUT_TOKENS
    try:
        tokens = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_OUTPUT_TOKENS
    return max(512, min(tokens, 16000))


def _resolve_thinking() -> dict[str, str] | None:
    value = str(os.environ.get("ARK_IMAGE_TO_SVG_THINKING") or "disabled").strip().lower()
    if value in {"", "default", "auto"}:
        return None
    if value not in {"disabled", "enabled"}:
        value = "disabled"
    return {"type": value}


def _validate_image_upload(image_bytes: bytes, mime_type: str) -> str:
    if not image_bytes:
        raise ValueError("请上传图片文件")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError("图片不能超过 6MB")
    mime = str(mime_type or "").split(";", 1)[0].strip().lower()
    if mime not in ALLOWED_IMAGE_MIME_TYPES:
        raise ValueError("只支持 PNG、JPG、WebP 或 GIF 图片")
    return mime


def _image_data_url(image_bytes: bytes, mime_type: str) -> str:
    mime = _validate_image_upload(image_bytes, mime_type)
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _otsu_threshold(gray: Any) -> int:
    histogram = gray.histogram()
    total = sum(histogram)
    if total <= 0:
        return 160

    sum_total = sum(level * count for level, count in enumerate(histogram))
    sum_back = 0
    weight_back = 0
    best_threshold = 160
    best_variance = -1.0
    for level, count in enumerate(histogram):
        weight_back += count
        if weight_back <= 0:
            continue
        weight_fore = total - weight_back
        if weight_fore <= 0:
            break
        sum_back += level * count
        mean_back = sum_back / weight_back
        mean_fore = (sum_total - sum_back) / weight_fore
        variance = weight_back * weight_fore * (mean_back - mean_fore) ** 2
        if variance > best_variance:
            best_variance = variance
            best_threshold = level
    return max(40, min(220, int(best_threshold)))


def _preprocess_image_for_face_svg(image_bytes: bytes, mime_type: str) -> tuple[bytes, str, dict[str, Any]]:
    source_mime = _validate_image_upload(image_bytes, mime_type)
    fallback_meta = {"applied": False, "mode": "original", "mime_type": source_mime}
    try:
        from PIL import Image, ImageOps  # type: ignore

        with Image.open(io.BytesIO(image_bytes)) as image:
            image = ImageOps.exif_transpose(image)
            if getattr(image, "is_animated", False):
                image.seek(0)
            gray = ImageOps.grayscale(image.convert("RGB"))
            gray = ImageOps.autocontrast(gray)
            threshold = _otsu_threshold(gray)
            black_white = gray.point(lambda pixel: 255 if pixel > threshold else 0, mode="L")
            output = io.BytesIO()
            black_white.save(output, format="PNG", optimize=True)
            return (
                output.getvalue(),
                "image/png",
                {
                    "applied": True,
                    "mode": "binary_bw",
                    "mime_type": "image/png",
                    "source_mime_type": source_mime,
                    "threshold": threshold,
                },
            )
    except Exception:
        return image_bytes, source_mime, fallback_meta


def _build_prompt(user_prompt: str) -> str:
    extra = str(user_prompt or "").strip()
    prompt = (
        "你是 Deskbot 小歪的图片表情包转译器。输入图会先被服务端转成高对比黑白图，"
        "请定位并提取面部表情，只保留眉眼、眼神、鼻子、嘴型和脸颊这些表达情绪的五官造型，"
        "忽略背景、文字、水印、边框、装饰、身体和手势，生成适合 284x240 OLED 显示的矢量表情。"
        "只输出 JSON，不要 Markdown，不要解释。"
        "JSON schema: {name, title, svg, scene}。"
        "svg 必须是单个 <svg viewBox=\"0 0 284 240\">，只使用 path/circle/ellipse/rect/line/polyline/polygon，"
        "不要 script、style、foreignObject、image 或外链资源。"
        "scene 必须是 Deskbot emotion scene：{name,title,frames:[{ms,elements}]}，"
        "frames 必须是 4 到 6 帧动画，每帧 80 到 900ms，且每帧 elements 必须是 object。"
        "elements 只能包含 eye_l/eye_r/nose/mouth/extra 数组；scene 图元 shape 只使用 "
        "ellipse_fill, ellipse, circle, line, rect, round_rect, round_rect_outline。"
        "scene 图元必须使用 PB 坐标字段：圆/椭圆用 x/y/r 或 x/y/rw/rh，矩形用 x/y/w/h/radius，"
        "线用 x1/y1/x2/y2；不要在 scene 里使用 cx/cy/rx/ry/path/svg。"
        "scene.name 必须匹配 ^[a-z][a-z0-9_]*$，用英文小写 snake_case，坐标范围基于 284x240。"
    )
    if extra:
        prompt += f"\n用户补充要求：{extra}"
    return prompt


def _post_ark_responses(url: str, payload: dict[str, Any], api_key: str, timeout: int) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "deskbot-server/0.1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", "replace").strip()
        preview = err_body[:1000] if err_body else str(exc)
        raise RuntimeError(f"Ark Responses 请求失败 HTTP {exc.code}: {preview}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ark Responses 请求失败: {exc.reason}") from exc
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        preview = raw[:1000].decode("utf-8", "replace")
        raise RuntimeError(f"Ark Responses 返回不是合法 JSON: {preview}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Ark Responses 返回格式异常：顶层不是 JSON object")
    return parsed


def _extract_response_text(response: dict[str, Any]) -> str:
    output_text = response.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    parts: list[str] = []
    output = response.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("text"), str):
                parts.append(item["text"])
            content = item.get("content")
            if isinstance(content, list):
                for chunk in content:
                    if isinstance(chunk, dict):
                        if isinstance(chunk.get("text"), str):
                            parts.append(chunk["text"])
                        elif isinstance(chunk.get("content"), str):
                            parts.append(chunk["content"])
            elif isinstance(content, str):
                parts.append(content)

    choices = response.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                parts.append(message["content"])

    return "\n".join(p for p in parts if p).strip()


def _json_object_from_text(text: str) -> dict[str, Any]:
    cleaned = _JSON_FENCE_RE.sub("", str(text or "").strip()).strip()
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        obj = json.loads(cleaned[start : end + 1])
    if not isinstance(obj, dict):
        raise ValueError("模型输出不是 JSON object")
    return obj


def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _safe_attr(name: str, value: str) -> str | None:
    if name.lower().startswith("on"):
        return None
    clean = str(value or "").strip()
    if not clean:
        return None
    if name in {"fill", "stroke"} and not _SAFE_COLOR_RE.match(clean):
        return None
    if name in {"href", "xlink:href", "style"}:
        return None
    return clean[:2000]


def _clean_svg_node(node: ET.Element) -> ET.Element | None:
    tag = _strip_namespace(str(node.tag))
    if tag not in _ALLOWED_SVG_TAGS:
        return None
    clean = ET.Element(tag)
    allowed = _ALLOWED_SVG_ATTRS.get(tag, set())
    for raw_name, raw_value in node.attrib.items():
        name = _strip_namespace(str(raw_name))
        if name not in allowed:
            continue
        value = _safe_attr(name, raw_value)
        if value is not None:
            clean.set(name, value)
    for child in list(node):
        child_clean = _clean_svg_node(child)
        if child_clean is not None:
            clean.append(child_clean)
    return clean


def sanitize_svg(svg: str) -> str:
    try:
        root = ET.fromstring(str(svg or "").strip())
    except ET.ParseError as exc:
        raise ValueError(f"SVG 解析失败: {exc}") from exc
    if _strip_namespace(str(root.tag)) != "svg":
        raise ValueError("模型输出的 svg 必须以 <svg> 为根节点")
    clean = _clean_svg_node(root)
    if clean is None:
        raise ValueError("SVG 内容为空")
    if not clean.get("viewBox"):
        clean.set("viewBox", "0 0 284 240")
    return ET.tostring(clean, encoding="unicode", short_empty_elements=True)


def _hashed_scene_name(*parts: Any) -> str:
    digest = hashlib.sha1()
    for part in parts:
        if isinstance(part, bytes):
            digest.update(part)
        else:
            digest.update(str(part or "").encode("utf-8", "surrogatepass"))
        digest.update(b"\0")
    return f"image_expr_{digest.hexdigest()[:12]}"


def _slug_name(value: str, fallback: str = "image_expression") -> str:
    raw = str(value or fallback).strip().lower()
    raw = _NAME_RE.sub("_", raw).strip("_")[:64]
    if not raw or not _VALID_SCENE_NAME_RE.match(raw):
        raw = fallback
    return raw


def _num(value: Any, fallback: float = 0) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return fallback
    return n if n == n else fallback


def _is_background_like(params: dict[str, Any]) -> bool:
    fill = str(params.get("fill") or params.get("color") or "").strip().lower()
    rw = _num(params.get("rx", params.get("rw", params.get("r"))), 0)
    rh = _num(params.get("ry", params.get("rh", params.get("r"))), 0)
    w = _num(params.get("width", params.get("w")), 0)
    h = _num(params.get("height", params.get("h")), 0)
    large = (rw >= 90 and rh >= 70) or (w >= 180 and h >= 140)
    if fill in {"#fff", "#ffffff", "white", "rgb(255,255,255)", "rgb(255, 255, 255)"}:
        return large
    return False


def _model_element_to_primitive(item: object) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    params = item.get("params") if isinstance(item.get("params"), dict) else item
    shape = str(item.get("shape") or item.get("type") or params.get("shape") or "").strip().lower()
    if not shape:
        return None
    if _is_background_like(params):
        return None
    if shape in {"ellipse", "ellipse_fill", "ellipse_outline"}:
        return {
            "shape": "ellipse" if "outline" in shape else "ellipse_fill",
            "x": round(_num(params.get("x", params.get("cx")), 0)),
            "y": round(_num(params.get("y", params.get("cy")), 0)),
            "rw": round(_num(params.get("rw", params.get("rx", params.get("r"))), 1)),
            "rh": round(_num(params.get("rh", params.get("ry", params.get("r"))), 1)),
        }
    if shape in {"circle", "circle_fill", "circle_outline"}:
        return {
            "shape": "circle_outline" if "outline" in shape else "circle",
            "x": round(_num(params.get("x", params.get("cx")), 0)),
            "y": round(_num(params.get("y", params.get("cy")), 0)),
            "r": round(_num(params.get("r"), 1)),
        }
    if shape == "line":
        return {
            "shape": "line",
            "x1": round(_num(params.get("x1"), 0)),
            "y1": round(_num(params.get("y1"), 0)),
            "x2": round(_num(params.get("x2"), 0)),
            "y2": round(_num(params.get("y2"), 0)),
            "sw": round(_num(params.get("stroke_width", params.get("sw")), 2), 2),
        }
    if shape in {"rect", "rect_fill", "round_rect_fill", "round_rect", "round_rect_outline", "rect_outline"}:
        w = _num(params.get("w", params.get("width")), 1)
        h = _num(params.get("h", params.get("height")), 1)
        x = _num(params.get("x"), _num(params.get("cx"), 0) - w / 2)
        y = _num(params.get("y"), _num(params.get("cy"), 0) - h / 2)
        outline = "outline" in shape
        round_rect = "round" in shape or params.get("rx") is not None or params.get("radius") is not None
        return {
            "shape": ("round_rect_outline" if outline else "round_rect") if round_rect else ("rect_outline" if outline else "rect"),
            "x": round(x),
            "y": round(y),
            "w": round(w),
            "h": round(h),
            "radius": round(_num(params.get("radius", params.get("rx", params.get("r"))), min(w, h) / 2)),
        }
    return None


def _primitive_center(item: dict[str, Any]) -> tuple[float, float]:
    if item.get("shape") == "line":
        return (
            (_num(item.get("x1"), 0) + _num(item.get("x2"), 0)) / 2,
            (_num(item.get("y1"), 0) + _num(item.get("y2"), 0)) / 2,
        )
    if "w" in item and "h" in item:
        return (_num(item.get("x"), 0) + _num(item.get("w"), 0) / 2, _num(item.get("y"), 0) + _num(item.get("h"), 0) / 2)
    return (_num(item.get("x"), 0), _num(item.get("y"), 0))


def _group_flat_model_elements(items: list[object]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {layer: [] for layer in SCENE_LAYERS}
    for raw in items:
        primitive = _model_element_to_primitive(raw)
        if not primitive:
            continue
        cx, cy = _primitive_center(primitive)
        if cy < 122:
            layer = "eye_l" if cx < 142 else "eye_r"
        elif cy < 140 and abs(cx - 142) < 28:
            layer = "nose"
        elif cy >= 130:
            layer = "mouth"
        else:
            layer = "extra"
        grouped[layer].append(primitive)
    return grouped


def _coerce_grouped_model_elements(elements: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {layer: [] for layer in SCENE_LAYERS}
    for layer in grouped:
        rows = elements.get(layer)
        if not isinstance(rows, list):
            continue
        for row in rows:
            primitive = _model_element_to_primitive(row)
            if primitive:
                grouped[layer].append(primitive)
    return grouped


def _shift_primitive(item: dict[str, Any], *, dx: int = 0, dy: int = 0) -> dict[str, Any]:
    out = copy.deepcopy(item)
    shape = str(out.get("shape") or "").strip().lower()
    if shape == "line":
        for key, delta in (("x1", dx), ("x2", dx), ("y1", dy), ("y2", dy)):
            if key in out:
                out[key] = round(_num(out.get(key), 0) + delta)
    else:
        if "x" in out:
            out["x"] = round(_num(out.get("x"), 0) + dx)
        if "y" in out:
            out["y"] = round(_num(out.get("y"), 0) + dy)
    return out


def _animate_primitive(item: dict[str, Any], *, layer: str, variant: int) -> dict[str, Any]:
    out = copy.deepcopy(item)
    shape = str(out.get("shape") or "").strip().lower()
    if layer in {"eye_l", "eye_r"} and shape in {"ellipse", "ellipse_fill"}:
        rh = max(1, round(_num(out.get("rh", out.get("r")), 4)))
        rw = max(1, round(_num(out.get("rw", out.get("r")), 4)))
        if variant == 1:
            out["rh"] = max(1, round(rh * 0.55))
            out["rw"] = max(1, round(rw * 1.04))
            out["y"] = round(_num(out.get("y"), 0) + 1)
        elif variant == 2:
            out["rh"] = max(1, rh + 2)
        else:
            out["y"] = round(_num(out.get("y"), 0) - 1)
        return out
    if layer == "mouth":
        if shape in {"ellipse", "ellipse_fill"}:
            rh = max(1, round(_num(out.get("rh", out.get("r")), 4)))
            out["rh"] = max(1, rh + (3 if variant == 2 else -2 if variant == 1 else 1))
            out["y"] = round(_num(out.get("y"), 0) + (1 if variant == 2 else 0))
        elif shape in {"round_rect", "round_rect_outline", "rect", "rect_outline"}:
            h = max(1, round(_num(out.get("h"), 4)))
            out["h"] = max(1, h + (4 if variant == 2 else -2 if variant == 1 else 1))
            out["radius"] = min(round(_num(out.get("radius", out.get("r")), out["h"] / 2)), max(1, out["h"] // 2))
        elif shape == "line":
            out = _shift_primitive(out, dy=(1 if variant == 2 else -1 if variant == 1 else 0))
        return out
    if layer == "extra":
        return _shift_primitive(out, dy=(-1 if variant == 1 else 1 if variant == 2 else 0))
    return out


def _animated_frame_from(frame: dict[str, Any], *, variant: int, ms: int) -> dict[str, Any]:
    elements = frame.get("elements") if isinstance(frame.get("elements"), dict) else {}
    next_elements: dict[str, list[dict[str, Any]]] = {layer: [] for layer in SCENE_LAYERS}
    for layer in SCENE_LAYERS:
        rows = elements.get(layer)
        if not isinstance(rows, list):
            continue
        next_elements[layer] = [
            _animate_primitive(row, layer=layer, variant=variant)
            for row in rows
            if isinstance(row, dict)
        ]
    return {"ms": ms, "elements": next_elements}


def _ensure_animation_frames(scene: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(scene)
    frames = [f for f in out.get("frames", []) if isinstance(f, dict)]
    if not frames:
        frames = _fallback_scene(fallback_name=out.get("name") or "image_expression", fallback_title=out.get("title") or "表情")["frames"]
    normalized_frames = []
    for frame in frames:
        next_frame = copy.deepcopy(frame)
        elements = next_frame.get("elements")
        if isinstance(elements, dict):
            next_frame["elements"] = _coerce_grouped_model_elements(elements)
        else:
            next_frame["elements"] = {layer: [] for layer in SCENE_LAYERS}
        normalized_frames.append(next_frame)
    while len(normalized_frames) < MIN_ANIMATION_FRAMES:
        base = normalized_frames[(len(normalized_frames) - 1) % len(normalized_frames)]
        variant = len(normalized_frames) % 3
        ms = 140 if variant == 1 else 220 if variant == 2 else 360
        normalized_frames.append(_animated_frame_from(base, variant=variant, ms=ms))
    out["frames"] = normalized_frames
    return out


def _coerce_model_scene(raw: dict[str, Any]) -> dict[str, Any]:
    scene = dict(raw)
    frames = scene.get("frames")
    if not isinstance(frames, list):
        return scene
    next_frames = []
    for frame in frames:
        if not isinstance(frame, dict):
            next_frames.append(frame)
            continue
        next_frame = dict(frame)
        elements = next_frame.get("elements")
        if isinstance(elements, list):
            next_frame["elements"] = _group_flat_model_elements(elements)
        elif isinstance(elements, dict):
            next_frame["elements"] = _coerce_grouped_model_elements(elements)
        next_frames.append(next_frame)
    scene["frames"] = next_frames
    return scene


def _fallback_scene(*, fallback_name: str, fallback_title: str) -> dict[str, Any]:
    return {
        "name": fallback_name,
        "title": fallback_title,
        "frames": [
            {
                "ms": 360,
                "elements": {
                    "eye_l": [
                        {"shape": "ellipse_fill", "x": 90, "y": 88, "rw": 18, "rh": 20},
                        {"shape": "line", "x1": 66, "y1": 62, "x2": 110, "y2": 74, "sw": 4},
                    ],
                    "eye_r": [
                        {"shape": "ellipse_fill", "x": 196, "y": 88, "rw": 18, "rh": 20},
                        {"shape": "line", "x1": 174, "y1": 74, "x2": 218, "y2": 62, "sw": 4},
                    ],
                    "nose": [{"shape": "circle", "x": 142, "y": 122, "r": 4}],
                    "mouth": [{"shape": "round_rect_outline", "x": 112, "y": 146, "w": 60, "h": 36, "radius": 18}],
                    "extra": [],
                },
            }
        ],
    }


def _normalize_scene(raw: object, *, fallback_name: str, fallback_title: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        scene = _fallback_scene(fallback_name=fallback_name, fallback_title=fallback_title)
    else:
        scene = _coerce_model_scene(raw)
    scene["name"] = _slug_name(str(scene.get("name") or fallback_name), fallback_name)
    scene["title"] = str(scene.get("title") or fallback_title or scene["name"]).strip()[:80]
    try:
        normalized = normalize_face_expr_scenes([scene])[0]
    except ValueError:
        fallback = _fallback_scene(fallback_name=scene["name"], fallback_title=scene["title"])
        normalized = normalize_face_expr_scenes([fallback])[0]
    animated = _ensure_animation_frames(normalized)
    return normalize_face_expr_scenes([animated])[0]


def _response_payload(model: str, image_url: str, prompt: str, *, max_output_tokens: int | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "max_output_tokens": _resolve_max_output_tokens(max_output_tokens),
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_image", "image_url": image_url},
                    {"type": "input_text", "text": _build_prompt(prompt)},
                ],
            }
        ],
    }
    thinking = _resolve_thinking()
    if thinking:
        payload["thinking"] = thinking
    return payload


def generate_face_svg_from_image(
    image_bytes: bytes,
    mime_type: str,
    *,
    prompt: str = "",
    api_key: str | None = None,
    model: str | None = None,
    responses_url: str | None = None,
    timeout: int = 90,
    max_output_tokens: int | None = None,
    transport: ArkTransport | None = None,
) -> dict[str, Any]:
    resolved_key = _resolve_api_key(api_key)
    resolved_model = _resolve_model(model)
    model_image_bytes, model_mime_type, preprocess_meta = _preprocess_image_for_face_svg(image_bytes, mime_type)
    image_url = _image_data_url(model_image_bytes, model_mime_type)
    payload = _response_payload(resolved_model, image_url, prompt, max_output_tokens=max_output_tokens)
    call = transport or _post_ark_responses
    response = call(str(responses_url or ARK_RESPONSES_URL), payload, resolved_key, timeout)
    raw_text = _extract_response_text(response)
    if not raw_text:
        raise RuntimeError("Ark Responses 没有返回文本内容")
    obj = _json_object_from_text(raw_text)
    fallback_name = _hashed_scene_name(image_bytes, prompt, obj.get("name"), obj.get("title"), obj.get("svg"))
    name = _slug_name(str(obj.get("name") or fallback_name), fallback=fallback_name)
    title = str(obj.get("title") or name).strip()[:80]
    svg = sanitize_svg(str(obj.get("svg") or ""))
    scene = _normalize_scene(obj.get("scene"), fallback_name=name, fallback_title=title)
    return {
        "ok": True,
        "name": scene["name"],
        "title": scene.get("title") or title,
        "svg": svg,
        "scene": scene,
        "raw": response,
        "model": resolved_model,
        "usage": response.get("usage") if isinstance(response.get("usage"), dict) else None,
        "image_preprocess": preprocess_meta,
    }
