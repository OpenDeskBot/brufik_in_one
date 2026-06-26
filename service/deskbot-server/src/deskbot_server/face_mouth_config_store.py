"""音素口型：读写 ``deskbot-face.json`` 的 ``phonemes`` 段（运行时适配层）。"""
from __future__ import annotations

import copy
from typing import Any, Optional

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
    import json

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


def load_face_mouth_cfg_file(
    *, seed_if_missing: bool = True, device_id: Optional[str] = None
) -> Optional[list[dict[str, Any]]]:
    from deskbot_server.face_design_store import (
        _load_face_design_cached,
        ensure_face_design_file,
        phonemes_to_mouth_groups,
    )

    if seed_if_missing:
        ensure_face_design_file(device_id=device_id)
    design = _load_face_design_cached(device_id=device_id)
    if not isinstance(design, dict):
        return None if not seed_if_missing else []
    return phonemes_to_mouth_groups(design)


def save_face_mouth_cfg_file(
    groups: list[dict[str, Any]], *, device_id: Optional[str] = None
) -> None:
    from deskbot_server.face_design_store import (
        apply_mouth_groups_to_design,
        ensure_face_design_file,
        save_face_design_file,
    )

    design = ensure_face_design_file(device_id=device_id)
    updated = apply_mouth_groups_to_design(design, groups)
    save_face_design_file(updated, device_id=device_id)


def groups_to_mouth_bundle(groups: list[dict[str, Any]]) -> dict[str, Any]:
    """供 ``phoneme_seq_to_anim_seq`` 使用的 face_bundle 片段。"""
    return {"mouth_by_phoneme_groups": normalize_face_mouth_groups(groups), "mouth_by_phoneme": {}}
