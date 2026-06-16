"""各设备最近一帧人脸快照（进程内，供 LLM 识别上下文）。

注册人名由调试页提交 ``jpeg_base64`` + ``points``，不再写盘 ``face_live_snapshot.json``。
"""
from __future__ import annotations

import threading
from typing import Any, Optional

from deskbot_server.face_identity import (
    compute_face_descriptor,
    descriptor_from_jpeg_base64,
    is_embedding_vector,
)

_lock = threading.Lock()
_snapshots: dict[str, dict[int, dict[str, Any]]] = {}


def update_device_faces(device_id: str, faces: list[dict[str, Any]]) -> None:
    device_id = str(device_id or "").strip()
    if not device_id:
        return
    by_id: dict[int, dict[str, Any]] = {}
    for face in faces or []:
        if not isinstance(face, dict):
            continue
        fid = face.get("face_id")
        if fid is None:
            continue
        by_id[int(fid)] = dict(face)
    with _lock:
        _snapshots[device_id] = by_id


def list_device_faces(device_id: str) -> dict[int, dict[str, Any]]:
    """返回设备最近一帧各 ``face_id`` 的快照（进程内缓存）。"""
    device_id = str(device_id or "").strip()
    if not device_id:
        return {}
    with _lock:
        mem = _snapshots.get(device_id)
    if not mem:
        return {}
    return {int(k): dict(v) for k, v in mem.items()}


def list_recognized_faces(device_id: str, *, limit: int = 5) -> list[dict[str, Any]]:
    """已匹配到姓名的人脸，按置信度降序，去重后最多 ``limit`` 条。"""
    device_id = str(device_id or "").strip()
    if not device_id:
        return []
    cap = max(1, min(int(limit), 20))
    faces = list_device_faces(device_id)
    best_by_key: dict[str, dict[str, Any]] = {}
    for fid, face in faces.items():
        if not isinstance(face, dict):
            continue
        name = str(face.get("person_name") or "").strip()
        if not name:
            continue
        person_id = face.get("person_id")
        dedupe_key = f"p:{int(person_id)}" if person_id is not None else f"n:{name}"
        score_raw = face.get("identity_score")
        try:
            score = round(float(score_raw), 3) if score_raw is not None else None
        except (TypeError, ValueError):
            score = None
        row = {
            "person_name": name,
            "identity_score": score,
            "face_id": int(fid),
        }
        if person_id is not None:
            try:
                row["person_id"] = int(person_id)
            except (TypeError, ValueError):
                pass
        prev = best_by_key.get(dedupe_key)
        prev_score = prev.get("identity_score") if prev else None
        if prev is None or (score or 0.0) > (prev_score or 0.0):
            best_by_key[dedupe_key] = row
    ranked = sorted(
        best_by_key.values(),
        key=lambda r: (-(r.get("identity_score") or 0.0), str(r.get("person_name") or "")),
    )
    return ranked[:cap]


def resolve_descriptor_from_payload(payload: dict[str, Any]) -> Optional[list[float]]:
    """注册 API：embedding 开启时需 ``jpeg_base64`` + ``points``；否则可用几何特征。"""
    from deskbot_server.camera_face_tune import get_face_embedding_enabled

    points = payload.get("points") if isinstance(payload.get("points"), list) else None
    landmarks = payload.get("landmarks") if isinstance(payload.get("landmarks"), list) else []
    embedding_enabled = get_face_embedding_enabled()

    jpeg_b64 = payload.get("jpeg_base64") or payload.get("frame_jpeg_base64")
    if embedding_enabled and points and isinstance(jpeg_b64, str) and jpeg_b64.strip():
        emb = descriptor_from_jpeg_base64(jpeg_b64, points, landmarks=landmarks)
        if emb is not None:
            return emb

    raw_desc = payload.get("face_descriptor")
    if isinstance(raw_desc, list) and len(raw_desc) >= 4:
        try:
            vec = [float(x) for x in raw_desc]
            if is_embedding_vector(vec):
                return vec
            if not embedding_enabled:
                return vec
        except (TypeError, ValueError):
            pass

    if points:
        if embedding_enabled:
            return None
        desc = compute_face_descriptor(points, landmarks)
        if desc is not None:
            return desc
    return None
