"""场景编排持久化（``data/scene_playbooks.json``，顶层数组）。

每条编排由 ``chunks[]`` 组成，与 PB 协议一致：每包（``pb_single`` / 一轮 ``pb_chunk``）
可独立携带口播、表情、舵机，按顺序串行下发。
"""
from __future__ import annotations

import copy
import json
import os
import re
import uuid
from typing import Any, Optional

from deskbot_server.constants import SCENE_PLAYBOOKS_FILE
from deskbot_server.device_data import resolve_json_path

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$", re.I)
_CLIP_MS_MIN = 40
_CLIP_MS_MAX = 120_000


def _new_clip_id() -> str:
    return uuid.uuid4().hex[:10]


def _normalize_ms(raw: object, *, default: int = 500) -> int:
    try:
        ms = int(raw)
    except (TypeError, ValueError):
        ms = default
    return max(_CLIP_MS_MIN, min(_CLIP_MS_MAX, ms))


def _normalize_expr_part(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    scene = str(raw.get("scene") or raw.get("name") or "").strip()
    if not scene:
        return None
    return {"scene": scene, "ms": _normalize_ms(raw.get("ms"))}


def _normalize_servo_part(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    preset = str(raw.get("preset") or "").strip()
    ms = _normalize_ms(raw.get("ms"))
    if preset:
        return {"preset": preset, "ms": ms}
    if raw.get("x") is not None or raw.get("y") is not None:
        try:
            return {
                "x": int(raw.get("x", 90)),
                "y": int(raw.get("y", 90)),
                "xm": int(raw.get("xm", 0)),
                "ym": int(raw.get("ym", 0)),
                "ms": ms,
            }
        except (TypeError, ValueError) as exc:
            raise ValueError("servo needs preset or x/y") from exc
    return None


def _normalize_chunk(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("chunk must be an object")
    cid = str(raw.get("id") or _new_clip_id()).strip() or _new_clip_id()
    text = str(raw.get("text") or "").strip()
    expr = _normalize_expr_part(raw.get("expr"))
    servo = _normalize_servo_part(raw.get("servo"))
    if not text and not expr and not servo:
        raise ValueError("chunk needs text, expr or servo")
    out: dict[str, Any] = {"id": cid, "text": text}
    if expr:
        out["expr"] = expr
    if servo:
        out["servo"] = servo
    return out


def _legacy_to_chunks(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """旧三轨格式 → ``chunks``（尽力转换，建议人工复核）。"""
    chunks: list[dict[str, Any]] = []
    for clip in raw.get("servo_track") or []:
        if not isinstance(clip, dict):
            continue
        servo = _normalize_servo_part(clip)
        if servo:
            chunks.append(
                {
                    "id": str(clip.get("id") or _new_clip_id()),
                    "text": "",
                    "servo": servo,
                }
            )
    for clip in raw.get("expr_track") or []:
        if not isinstance(clip, dict):
            continue
        expr = _normalize_expr_part(clip)
        if expr:
            chunks.append(
                {
                    "id": str(clip.get("id") or _new_clip_id()),
                    "text": "",
                    "expr": expr,
                }
            )
    for clip in raw.get("text_track") or []:
        if not isinstance(clip, dict):
            continue
        t = str(clip.get("text") or "").strip()
        if t:
            chunks.append({"id": str(clip.get("id") or _new_clip_id()), "text": t})
    legacy = str(raw.get("text") or "").strip()
    if legacy:
        chunks.append({"id": _new_clip_id(), "text": legacy})
    return chunks


def normalize_playbook(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("playbook must be an object")
    name = str(raw.get("name") or raw.get("id") or "").strip()
    if not name:
        raise ValueError("name required")
    if not _NAME_RE.match(name):
        raise ValueError(f"invalid name {name!r}")
    title = str(raw.get("title") or name).strip()

    chunks_raw = raw.get("chunks")
    if isinstance(chunks_raw, list) and chunks_raw:
        chunks = [_normalize_chunk(c) for c in chunks_raw]
    else:
        chunks = _legacy_to_chunks(raw)
    if not chunks:
        raise ValueError("playbook needs at least one chunk")

    return {"name": name, "title": title, "chunks": chunks}


def normalize_scene_playbooks(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError("body must be a JSON array")
    return [normalize_playbook(x) for x in raw]


def _seed_default_playbooks() -> list[dict[str, Any]]:
    return [
        {
            "name": "demo_greet",
            "title": "演示问候",
            "chunks": [
                {"id": "c1", "text": "", "servo": {"preset": "look_left", "ms": 500}},
                {"id": "c2", "text": "", "servo": {"preset": "center", "ms": 500}},
                {
                    "id": "c3",
                    "text": "你好，很高兴见到你",
                    "expr": {"scene": "happy_smile", "ms": 1500},
                },
            ],
        },
    ]


def load_scene_playbooks_file(
    *, seed_if_missing: bool = True, device_id: Optional[str] = None
) -> Optional[list[dict[str, Any]]]:
    path = resolve_json_path(SCENE_PLAYBOOKS_FILE, device_id)
    if not os.path.isfile(path):
        if not seed_if_missing:
            return None
        rows = _seed_default_playbooks()
        save_scene_playbooks_file(rows, device_id=device_id)
        return rows
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return normalize_scene_playbooks(raw)


def save_scene_playbooks_file(
    rows: list[dict[str, Any]], *, device_id: Optional[str] = None
) -> None:
    norm = normalize_scene_playbooks(rows)
    path = resolve_json_path(SCENE_PLAYBOOKS_FILE, device_id)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(norm, f, ensure_ascii=False, indent=2)
        f.write("\n")


def find_playbook_by_name(rows: list[dict[str, Any]], name: str) -> Optional[dict[str, Any]]:
    want = str(name or "").strip().lower()
    if not want:
        return None
    for row in rows:
        if str(row.get("name") or "").strip().lower() == want:
            return copy.deepcopy(row)
    return None


def collect_missing_servo_presets(
    playbooks: list[dict[str, Any]] | dict[str, Any],
    *,
    device_id: Optional[str] = None,
) -> list[str]:
    """编排引用的 ``servo.preset`` 在 ``servo.json`` 中不存在时返回 id 列表。"""
    from deskbot_server.pb.llm_plan import _resolve_servo_preset_steps

    rows = playbooks if isinstance(playbooks, list) else [playbooks]
    missing: set[str] = set()
    for pb in rows:
        if not isinstance(pb, dict):
            continue
        for chunk in pb.get("chunks") or []:
            if not isinstance(chunk, dict):
                continue
            servo = chunk.get("servo")
            if not isinstance(servo, dict):
                continue
            preset = str(servo.get("preset") or "").strip()
            if not preset:
                continue
            if not _resolve_servo_preset_steps(preset, device_id=device_id):
                missing.add(preset)
    return sorted(missing)
