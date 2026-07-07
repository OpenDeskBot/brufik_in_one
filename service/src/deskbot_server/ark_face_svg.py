"""Ark Responses image-to-face SVG integration."""
from __future__ import annotations

import base64
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
MAX_IMAGE_BYTES = 6 * 1024 * 1024
ALLOWED_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}

ArkTransport = Callable[[str, dict[str, Any], str, int], dict[str, Any]]

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)
_NAME_RE = re.compile(r"[^a-z0-9_]+", re.I)
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


def _image_data_url(image_bytes: bytes, mime_type: str) -> str:
    if not image_bytes:
        raise ValueError("请上传图片文件")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError("图片不能超过 6MB")
    mime = str(mime_type or "").split(";", 1)[0].strip().lower()
    if mime not in ALLOWED_IMAGE_MIME_TYPES:
        raise ValueError("只支持 PNG、JPG、WebP 或 GIF 图片")
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _build_prompt(user_prompt: str) -> str:
    extra = str(user_prompt or "").strip()
    prompt = (
        "你是 Deskbot 小歪的图片表情包转译器。请观察上传的表情包图片，"
        "提取最核心的情绪和五官造型，生成适合 284x240 OLED 显示的矢量表情。"
        "只输出 JSON，不要 Markdown，不要解释。"
        "JSON schema: {name, title, svg, scene}。"
        "svg 必须是单个 <svg viewBox=\"0 0 284 240\">，只使用 path/circle/ellipse/rect/line/polyline/polygon，"
        "不要 script、style、foreignObject、image 或外链资源。"
        "scene 必须是 Deskbot emotion scene：{name,title,frames:[{ms,elements}]}，"
        "elements 可包含 eye_l/eye_r/nose/mouth/extra 数组；shape 只使用 ellipse_fill, circle_fill, "
        "line, round_rect_outline, round_rect_fill。"
        "scene.name 用英文小写 snake_case，至少 1 帧，坐标范围基于 284x240。"
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


def _slug_name(value: str, fallback: str = "image_expression") -> str:
    raw = str(value or fallback).strip().lower()
    raw = _NAME_RE.sub("_", raw).strip("_")[:64]
    if not raw or not re.match(r"^[a-z]", raw):
        raw = fallback
    return raw


def _normalize_scene(raw: object, *, fallback_name: str, fallback_title: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("模型输出缺少 scene")
    scene = dict(raw)
    scene["name"] = _slug_name(str(scene.get("name") or fallback_name), fallback_name)
    scene["title"] = str(scene.get("title") or fallback_title or scene["name"]).strip()[:80]
    return normalize_face_expr_scenes([scene])[0]


def generate_face_svg_from_image(
    image_bytes: bytes,
    mime_type: str,
    *,
    prompt: str = "",
    api_key: str | None = None,
    model: str | None = None,
    responses_url: str | None = None,
    timeout: int = 90,
    transport: ArkTransport | None = None,
) -> dict[str, Any]:
    resolved_key = _resolve_api_key(api_key)
    resolved_model = _resolve_model(model)
    image_url = _image_data_url(image_bytes, mime_type)
    payload = {
        "model": resolved_model,
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
    call = transport or _post_ark_responses
    response = call(str(responses_url or ARK_RESPONSES_URL), payload, resolved_key, timeout)
    raw_text = _extract_response_text(response)
    if not raw_text:
        raise RuntimeError("Ark Responses 没有返回文本内容")
    obj = _json_object_from_text(raw_text)
    name = _slug_name(str(obj.get("name") or "image_expression"))
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
    }
