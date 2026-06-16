"""已注册人脸档案持久化（``data/face_profiles.json``）。"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from deskbot_server.constants import FACE_PROFILES_FILE
from deskbot_server.device_data import resolve_json_path
from deskbot_server.face_identity import (
    descriptor_cosine_similarity,
    ema_update_descriptor,
    is_embedding_vector,
    is_legacy_geometric_vector,
)


def _normalize_profile(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("profile must be object")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("name required")
    desc_raw = raw.get("descriptor")
    if not isinstance(desc_raw, list) or len(desc_raw) < 4:
        raise ValueError("descriptor must be a float vector")
    descriptor = [float(x) for x in desc_raw]
    kind = str(raw.get("descriptor_kind") or "").strip().lower()
    if not kind:
        kind = "embedding" if is_embedding_vector(descriptor) else "geometry"
    pid = int(raw.get("person_id", 0))
    if pid <= 0:
        raise ValueError("person_id must be positive")
    return {
        "person_id": pid,
        "name": name,
        "descriptor": descriptor,
        "descriptor_kind": kind,
    }


def load_face_profiles(*, device_id: Optional[str] = None) -> list[dict[str, Any]]:
    path = resolve_json_path(FACE_PROFILES_FILE, device_id)
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, dict):
        items = raw.get("profiles") or []
    elif isinstance(raw, list):
        items = raw
    else:
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        try:
            out.append(_normalize_profile(item))
        except (TypeError, ValueError):
            continue
    return out


def save_face_profiles(profiles: list[dict[str, Any]], *, device_id: Optional[str] = None) -> None:
    path = resolve_json_path(FACE_PROFILES_FILE, device_id)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    cleaned = [_normalize_profile(p) for p in profiles]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"profiles": cleaned}, f, ensure_ascii=False, indent=2)
        f.write("\n")


def next_person_id(profiles: list[dict[str, Any]]) -> int:
    if not profiles:
        return 1
    return max(int(p["person_id"]) for p in profiles) + 1


def _same_descriptor_space(a: list[float], b: list[float]) -> bool:
    ae = is_embedding_vector(a)
    be = is_embedding_vector(b)
    if ae or be:
        return ae and be
    return is_legacy_geometric_vector(a) and is_legacy_geometric_vector(b)


def best_profile_similarity(
    profiles: list[dict[str, Any]],
    descriptor: list[float],
) -> tuple[Optional[dict[str, Any]], float]:
    """返回最相似档案（不设阈值）；仅比较同类型向量。"""
    best: Optional[dict[str, Any]] = None
    best_sim = -1.0
    for p in profiles:
        pd = p.get("descriptor")
        if not isinstance(pd, list):
            continue
        if not _same_descriptor_space(descriptor, pd):
            continue
        sim = descriptor_cosine_similarity(descriptor, pd)
        if sim > best_sim:
            best_sim = sim
            best = p
    return best, best_sim


def find_profile_by_similarity(
    profiles: list[dict[str, Any]],
    descriptor: list[float],
    *,
    threshold: float,
) -> tuple[Optional[dict[str, Any]], float]:
    best, best_sim = best_profile_similarity(profiles, descriptor)
    if best is not None and best_sim >= threshold:
        return best, best_sim
    return None, best_sim


def resolve_profile_match(
    profiles: list[dict[str, Any]],
    descriptor: list[float],
    *,
    match_threshold: float,
    keep_threshold: float,
    locked_person_id: Optional[int] = None,
) -> tuple[Optional[dict[str, Any]], float]:
    """档案匹配：已锁定 person 时用更低阈值保持，避免转头时 person_id 闪烁。"""
    best, best_sim = best_profile_similarity(profiles, descriptor)
    if locked_person_id is not None:
        for p in profiles:
            if int(p["person_id"]) == int(locked_person_id):
                sim = descriptor_cosine_similarity(descriptor, p["descriptor"])
                if sim >= keep_threshold:
                    return p, sim
                return None, best_sim
    if best is not None and best_sim >= match_threshold:
        return best, best_sim
    return None, best_sim


def list_face_profiles_summary(*, device_id: Optional[str] = None) -> list[dict[str, Any]]:
    """列表展示用：不含 descriptor 向量。"""
    return [
        {
            "person_id": int(p["person_id"]),
            "name": str(p["name"]),
            "descriptor_kind": str(p.get("descriptor_kind") or ""),
        }
        for p in load_face_profiles(device_id=device_id)
    ]


def delete_face_profile(person_id: int, *, device_id: Optional[str] = None) -> bool:
    """按 ``person_id`` 删除已注册人脸档案。"""
    try:
        pid = int(person_id)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    profiles = load_face_profiles(device_id=device_id)
    kept = [p for p in profiles if int(p["person_id"]) != pid]
    if len(kept) == len(profiles):
        return False
    save_face_profiles(kept, device_id=device_id)
    return True


def upsert_profile(
    profiles: list[dict[str, Any]],
    *,
    name: str,
    descriptor: list[float],
    person_id: Optional[int] = None,
    merge_threshold: float = 0.88,
) -> dict[str, Any]:
    """注册或合并同名/相似档案，返回最终 profile。"""
    name = str(name).strip()
    if not name:
        raise ValueError("name required")
    matched, sim = find_profile_by_similarity(
        profiles, descriptor, threshold=merge_threshold
    )
    kind = "embedding" if is_embedding_vector(descriptor) else "geometry"
    if matched is not None and matched.get("name") == name:
        matched["descriptor"] = ema_update_descriptor(matched["descriptor"], descriptor, alpha=0.35)
        matched["descriptor_kind"] = kind
        return matched
    if matched is not None and sim >= 0.95:
        matched["name"] = name
        matched["descriptor"] = ema_update_descriptor(matched["descriptor"], descriptor, alpha=0.35)
        matched["descriptor_kind"] = kind
        return matched
    pid = int(person_id) if person_id else next_person_id(profiles)
    profile = {
        "person_id": pid,
        "name": name,
        "descriptor": list(descriptor),
        "descriptor_kind": kind,
    }
    profiles.append(profile)
    return profile
