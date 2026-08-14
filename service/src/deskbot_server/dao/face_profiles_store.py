"""已注册人脸档案持久化（``device_profile_face`` 表）。"""

from __future__ import annotations

import json
import threading
from typing import Any

from deskbot_server.dao import device_profile_face_mapper as mapper
from deskbot_server.vision.face_identity import (
    descriptor_cosine_similarity,
    ema_update_descriptor,
    is_embedding_vector,
    is_legacy_geometric_vector,
)

# ────────────────── 版本计数（供 FaceTracker 热加载）──────────────────

_version: int = 0
_version_lock = threading.Lock()


def _bump_version() -> None:
    global _version
    with _version_lock:
        _version += 1


def get_version() -> int:
    with _version_lock:
        return _version


# ────────────────── 加载 ─────────────────────────


def _row_to_dict(row) -> dict[str, Any]:
    """将 ORM 行转为与旧 JSON 格式兼容的 dict（``id`` 替代 ``person_id``）。"""
    descriptor = json.loads(row.descriptor) if isinstance(row.descriptor, str) else row.descriptor
    return {
        "id": int(row.id),
        "name": str(row.name),
        "descriptor": descriptor,
        "descriptor_kind": str(row.descriptor_kind or ""),
    }


def load_face_profiles(*, device_id: str | None = None) -> list[dict[str, Any]]:
    if not device_id:
        return []
    rows = mapper.list_by_device(str(device_id))
    return [_row_to_dict(r) for r in rows]


def list_face_profiles_summary(*, device_id: str | None = None) -> list[dict[str, Any]]:
    """列表展示用：不含 descriptor 向量。"""
    return [
        {
            "id": int(p["id"]),
            "name": str(p["name"]),
            "descriptor_kind": str(p.get("descriptor_kind") or ""),
        }
        for p in load_face_profiles(device_id=device_id)
    ]


# ────────────────── 向量工具 ─────────────────────────


def _same_descriptor_space(a: list[float], b: list[float]) -> bool:
    ae = is_embedding_vector(a)
    be = is_embedding_vector(b)
    if ae or be:
        return ae and be
    return is_legacy_geometric_vector(a) and is_legacy_geometric_vector(b)


def best_profile_similarity(
    profiles: list[dict[str, Any]], descriptor: list[float]
) -> tuple[dict[str, Any] | None, float]:
    """返回最相似档案（不设阈值）；仅比较同类型向量。"""
    best: dict[str, Any] | None = None
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
    profiles: list[dict[str, Any]], descriptor: list[float], *, threshold: float
) -> tuple[dict[str, Any] | None, float]:
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
    locked_profile_id: int | None = None,
) -> tuple[dict[str, Any] | None, float]:
    """档案匹配：已锁定 person 时用更低阈值保持，避免转头时 id 闪烁。"""
    best, best_sim = best_profile_similarity(profiles, descriptor)
    if locked_profile_id is not None:
        for p in profiles:
            if int(p["id"]) == int(locked_profile_id):
                sim = descriptor_cosine_similarity(descriptor, p["descriptor"])
                if sim >= keep_threshold:
                    return p, sim
                return None, best_sim
    if best is not None and best_sim >= match_threshold:
        return best, best_sim
    return None, best_sim


# ────────────────── 写操作 ─────────────────────────


def upsert_profile(
    device_id: str,
    *,
    name: str,
    descriptor: list[float],
    merge_threshold: float = 0.88,
) -> dict[str, Any]:
    """注册或合并同名/相似档案，写入 DB 并返回最终 profile dict。"""
    name = str(name).strip()
    if not name:
        raise ValueError("name required")

    profiles = load_face_profiles(device_id=device_id)
    matched, sim = find_profile_by_similarity(profiles, descriptor, threshold=merge_threshold)
    kind = "embedding" if is_embedding_vector(descriptor) else "geometry"
    desc_json = json.dumps(list(descriptor), ensure_ascii=False)

    if matched is not None and matched.get("name") == name:
        merged = ema_update_descriptor(matched["descriptor"], descriptor, alpha=0.35)
        merged_json = json.dumps(merged, ensure_ascii=False)
        mapper.update(int(matched["id"]), name, merged_json, kind)
        _bump_version()
        return {"id": int(matched["id"]), "name": name, "descriptor": merged, "descriptor_kind": kind}

    if matched is not None and sim >= 0.95:
        merged = ema_update_descriptor(matched["descriptor"], descriptor, alpha=0.35)
        merged_json = json.dumps(merged, ensure_ascii=False)
        mapper.update(int(matched["id"]), name, merged_json, kind)
        _bump_version()
        return {"id": int(matched["id"]), "name": name, "descriptor": merged, "descriptor_kind": kind}

    mapper.insert(str(device_id), name, desc_json, kind)
    _bump_version()
    inserted = mapper._last_inserted()
    new_id = int(inserted.id) if inserted else 0
    return {"id": new_id, "name": name, "descriptor": list(descriptor), "descriptor_kind": kind}


def delete_face_profile(profile_id: int, *, device_id: str | None = None) -> bool:
    """按 ``id`` 删除已注册人脸档案。"""
    try:
        pid = int(profile_id)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    row = mapper.get_by_id(pid)
    if row is None:
        return False
    if device_id and str(row.device_id) != str(device_id):
        return False
    mapper.delete_by_id(pid)
    _bump_version()
    return True


def update_face_profile_name(
    profile_id: int, name: str, *, device_id: str | None = None
) -> dict[str, Any] | None:
    """更新已注册人脸档案名称，返回摘要；档案不存在时返回 ``None``。"""
    try:
        pid = int(profile_id)
    except (TypeError, ValueError):
        return None
    clean_name = str(name or "").strip()
    if pid <= 0 or not clean_name:
        return None
    row = mapper.get_by_id(pid)
    if row is None:
        return None
    if device_id and str(row.device_id) != str(device_id):
        return None
    mapper.update_name(pid, clean_name)
    _bump_version()
    return {
        "id": int(row.id),
        "name": clean_name,
        "descriptor_kind": str(row.descriptor_kind or ""),
    }
