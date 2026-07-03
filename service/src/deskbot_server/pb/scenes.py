from __future__ import annotations

import copy
from typing import Any, Optional

from deskbot_server.face_expr_scenes_store import (
    design_frames_to_pb_chain,
    find_design_scene_by_name,
    load_face_expr_scenes_file,
)
from deskbot_server.pb.shapes import (
    PB_ACTION_APPEND,
    PB_LEVEL_DEBUG,
    apply_pb_dispatch_fields,
)


def _load_design_scenes_rows(*, device_id: Optional[str] = None) -> list[dict[str, Any]]:
    return load_face_expr_scenes_file(seed_if_missing=True, device_id=device_id) or []


def _load_pb_scenes_document() -> dict:
    """兼容旧调用：返回 ``{ "scenes": { name: { frames, ... } } }`` 形结构。"""
    rows = _load_design_scenes_rows()
    scenes: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        scenes[name] = {
            "id": name,
            "title_zh": row.get("title") or name,
            "frames": copy.deepcopy(row.get("frames") or []),
        }
    return {"scenes": scenes}


def _pb_scenes_root(doc: dict) -> dict:
    sc = doc.get("scenes")
    return sc if isinstance(sc, dict) else {}


def _pb_scene_entry_by_name(
    doc: dict,
    scene_lower: str,
    *,
    device_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """按 **不区分大小写** 匹配 ``deskbot-face.json`` 中的表情场景。"""
    _ = doc
    return find_design_scene_by_name(
        _load_design_scenes_rows(device_id=device_id), scene_lower
    )


def _pb_scene_keys_sorted(doc: dict | None = None) -> list[str]:
    """返回含非空 ``frames`` 的场景 name 列表（排序）。"""
    _ = doc
    out: list[str] = []
    for row in _load_design_scenes_rows():
        name = str(row.get("name") or "").strip()
        frames = row.get("frames")
        if name and isinstance(frames, list) and frames:
            out.append(name)
    out.sort(key=lambda s: (s.lower(), s))
    return out


def _prepare_pb_scene_chain_frames(
    scene_name: str, *, runtime_req: str, device_id: Optional[str] = None
) -> list[dict]:
    """从 ``deskbot-face.json`` 生成 pb 链，``append`` + 调试 ``level=3``。"""
    ent = find_design_scene_by_name(_load_design_scenes_rows(device_id=device_id), scene_name)
    if ent is None:
        return []
    pairs = design_frames_to_pb_chain(ent.get("frames") or [], runtime_req=runtime_req)
    if not pairs:
        return []
    msgs = [msg for msg, _pcm in pairs]
    apply_pb_dispatch_fields(msgs, action=PB_ACTION_APPEND, level=PB_LEVEL_DEBUG)
    return msgs
