"""音素口型配置持久化（``data/face_mouth_by_phoneme.json``，顶层为数组）。"""
from __future__ import annotations

import copy
import json
import os
from typing import Any, Optional

from deskbot_server.constants import FACE_MOUTH_BY_PHONEME_FILE
from deskbot_server.device_data import resolve_json_path
from deskbot_server.pb.shapes import is_mouth_phoneme_group_entry, simplify_phoneme_key


def _normalize_group(raw: object) -> dict[str, Any]:
    if not is_mouth_phoneme_group_entry(raw):
        raise ValueError("entry requires states[] + elements[]")
    entry = raw if isinstance(raw, dict) else {}
    states = simplify_group_states(entry.get("states") or [])
    if not states:
        raise ValueError("entry requires non-empty states after simplify")
    elements = copy.deepcopy(entry.get("elements"))
    if not isinstance(elements, list):
        raise ValueError("elements must be an array")
    off_raw = entry.get("offset")
    if off_raw is None:
        offset = {"x": 0, "y": 0}
    elif isinstance(off_raw, dict):
        try:
            offset = {"x": int(off_raw.get("x", 0)), "y": int(off_raw.get("y", 0))}
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid offset") from exc
    else:
        raise ValueError("offset must be an object")
    return {"states": states, "elements": elements, "offset": offset}


def simplify_group_states(states: list[Any]) -> list[str]:
    """去声调、合并停顿标记（``sp1`` 等 → ``_``），去重排序。"""
    seen: set[str] = set()
    out: list[str] = []
    for raw in states or []:
        key = simplify_phoneme_key(str(raw))
        if key and key not in seen:
            seen.add(key)
            out.append(key)

    def _sort_key(a: str) -> tuple[int, str]:
        if a == "_":
            return (-1, "")
        return (0, a)

    return sorted(out, key=_sort_key)


def _group_signature(group: dict[str, Any]) -> str:
    return json.dumps(
        [group["elements"], group["offset"]],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def collapse_simplified_groups(groups: list[Any]) -> list[dict[str, Any]]:
    """合并图元+offset 相同的组，states 取并集。"""
    sig_map: dict[str, dict[str, Any]] = {}
    for raw in groups or []:
        if not is_mouth_phoneme_group_entry(raw):
            continue
        g = _normalize_group(raw)
        sig = _group_signature(g)
        bucket = sig_map.get(sig)
        if bucket is None:
            bucket = {
                "states": [],
                "elements": g["elements"],
                "offset": dict(g["offset"]),
            }
            sig_map[sig] = bucket
        for st in g["states"]:
            if st not in bucket["states"]:
                bucket["states"].append(st)
    out = list(sig_map.values())
    for g in out:
        g["states"] = simplify_group_states(g["states"])
    out.sort(key=lambda g: (0 if "_" in g["states"] else 1, g["states"][0] if g["states"] else ""))
    return out


def normalize_face_mouth_groups(raw: object) -> list[dict[str, Any]]:
    """接受顶层数组，或旧版 ``{ mouth_by_phoneme_groups: [...] }``。"""
    groups_raw: list[Any]
    if isinstance(raw, list):
        groups_raw = raw
    elif isinstance(raw, dict):
        inner = raw.get("mouth_by_phoneme_groups")
        groups_raw = inner if isinstance(inner, list) else []
    else:
        raise ValueError("body must be a JSON array or legacy object with mouth_by_phoneme_groups")
    return [_normalize_group(g) for g in groups_raw]


def _face_defaults_from_default_expr_scene() -> dict[str, Any]:
    from deskbot_server.face_expr_scenes_store import (
        default_speech_blink_scene,
        find_design_scene_by_name,
        load_face_expr_scenes_file,
    )

    rows = load_face_expr_scenes_file(seed_if_missing=False) or []
    ent = find_design_scene_by_name(rows, "default") or default_speech_blink_scene()
    frames = ent.get("frames") if isinstance(ent, dict) else []
    elements: dict[str, Any] = {}
    if frames and isinstance(frames[0], dict):
        raw = frames[0].get("elements")
        elements = raw if isinstance(raw, dict) else {}
    return {
        "nose": copy.deepcopy(elements.get("nose") if isinstance(elements.get("nose"), list) else []),
        "eye_l": copy.deepcopy(elements.get("eye_l") if isinstance(elements.get("eye_l"), list) else []),
        "eye_r": copy.deepcopy(elements.get("eye_r") if isinstance(elements.get("eye_r"), list) else []),
    }


def _seed_default_mouth_groups() -> list[dict[str, Any]]:
    from deskbot_server.pb.face_bundle import default_pb_face_bundle
    from deskbot_server.pb.shapes import _normalize_mouth_entry

    mb = default_pb_face_bundle().get("mouth_by_phoneme") or {}
    raw_groups: list[dict[str, Any]] = []
    if isinstance(mb, dict):
        for ph, val in mb.items():
            pk = str(ph).strip()
            if not pk:
                continue
            ent = _normalize_mouth_entry(val)
            raw_groups.append(
                {
                    "states": [pk],
                    "elements": ent.get("elements") or [],
                    "offset": dict(ent.get("offset") or {"x": 0, "y": 0}),
                }
            )
    return collapse_simplified_groups(raw_groups)


def load_face_mouth_cfg_file(
    *, seed_if_missing: bool = True, device_id: Optional[str] = None
) -> Optional[list[dict[str, Any]]]:
    path = resolve_json_path(FACE_MOUTH_BY_PHONEME_FILE, device_id)
    if not os.path.isfile(path):
        if not seed_if_missing:
            return None
        groups = _seed_default_mouth_groups()
        save_face_mouth_cfg_file(groups, device_id=device_id)
        return groups
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    groups = normalize_face_mouth_groups(raw)
    if isinstance(raw, list):
        return groups
    # 旧版 keyed JSON 或带声调 states：读入后规范化并回写
    save_face_mouth_cfg_file(groups, device_id=device_id)
    return groups


def save_face_mouth_cfg_file(
    groups: list[dict[str, Any]], *, device_id: Optional[str] = None
) -> None:
    norm = normalize_face_mouth_groups(groups)
    path = resolve_json_path(FACE_MOUTH_BY_PHONEME_FILE, device_id)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(norm, f, ensure_ascii=False, indent=2)
        f.write("\n")


def face_mouth_api_payload(groups: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "config": groups,
        "face_defaults": _face_defaults_from_default_expr_scene(),
    }


def groups_to_mouth_bundle(groups: list[dict[str, Any]]) -> dict[str, Any]:
    """供 ``phoneme_seq_to_anim_seq`` 使用的 face_bundle 片段。"""
    return {"mouth_by_phoneme_groups": normalize_face_mouth_groups(groups), "mouth_by_phoneme": {}}
