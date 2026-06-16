"""场景编排持久化（``data/scene_playbooks.json``，顶层数组）。"""
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


def _normalize_text_clip(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("text clip must be an object")
    text = str(raw.get("text") or "").strip()
    if not text:
        raise ValueError("text clip.text required")
    cid = str(raw.get("id") or _new_clip_id()).strip() or _new_clip_id()
    return {"id": cid, "text": text, "ms": _normalize_ms(raw.get("ms"))}


def _normalize_expr_clip(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("expr clip must be an object")
    scene = str(raw.get("scene") or raw.get("name") or "").strip()
    if not scene:
        raise ValueError("expr clip.scene required")
    cid = str(raw.get("id") or _new_clip_id()).strip() or _new_clip_id()
    return {"id": cid, "scene": scene, "ms": _normalize_ms(raw.get("ms"))}


def _normalize_servo_clip(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("servo clip must be an object")
    cid = str(raw.get("id") or _new_clip_id()).strip() or _new_clip_id()
    preset = str(raw.get("preset") or "").strip()
    out: dict[str, Any] = {"id": cid, "ms": _normalize_ms(raw.get("ms"))}
    if preset:
        out["preset"] = preset
        return out
    try:
        out["x"] = int(raw.get("x", 90))
        out["y"] = int(raw.get("y", 90))
        out["xm"] = int(raw.get("xm", 0))
        out["ym"] = int(raw.get("ym", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("servo clip needs preset or x/y") from exc
    return out


def _normalize_track(clips_raw: object, normalizer) -> list[dict[str, Any]]:
    if not isinstance(clips_raw, list):
        return []
    return [normalizer(c) for c in clips_raw]


def normalize_playbook(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("playbook must be an object")
    name = str(raw.get("name") or raw.get("id") or "").strip()
    if not name:
        raise ValueError("name required")
    if not _NAME_RE.match(name):
        raise ValueError(f"invalid name {name!r}")
    title = str(raw.get("title") or name).strip()
    text = str(raw.get("text") or "").strip()
    text_track = _normalize_track(raw.get("text_track"), _normalize_text_clip)
    expr_track = _normalize_track(raw.get("expr_track"), _normalize_expr_clip)
    servo_track = _normalize_track(raw.get("servo_track"), _normalize_servo_clip)
    if not text and not text_track and not expr_track and not servo_track:
        raise ValueError("playbook needs text, text_track, expr_track or servo_track")
    return {
        "name": name,
        "title": title,
        "text": text,
        "text_track": text_track,
        "expr_track": expr_track,
        "servo_track": servo_track,
    }


def normalize_scene_playbooks(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError("body must be a JSON array")
    return [normalize_playbook(x) for x in raw]


def _seed_default_playbooks() -> list[dict[str, Any]]:
    return [
        {
            "name": "demo_greet",
            "title": "演示问候",
            "text": "你好，很高兴见到你",
            "text_track": [],
            "expr_track": [
                {"id": "e1", "scene": "happy_smile", "ms": 800},
                {"id": "e2", "scene": "default", "ms": 1500},
            ],
            "servo_track": [
                {"id": "s1", "preset": "look_left", "ms": 500},
                {"id": "s2", "preset": "center", "ms": 500},
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
