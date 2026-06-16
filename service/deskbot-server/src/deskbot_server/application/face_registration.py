"""从摄像头快照注册人脸档案。"""
from __future__ import annotations

from typing import Any, Optional

from deskbot_server.application.face_tracker import reload_all_trackers
from deskbot_server.camera_face_config_store import load_camera_face_cfg_file
from deskbot_server.face_profiles_store import load_face_profiles, save_face_profiles, upsert_profile
from deskbot_server.face_snapshot_cache import list_device_faces, resolve_descriptor_from_payload


def register_face_for_device(
    device_id: str,
    name: str,
    *,
    face_id: Optional[int] = None,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """将当前帧 ``face_id`` 的人脸写入 ``face_profiles.json``。"""
    device_id = str(device_id or "").strip()
    name = str(name or "").strip()
    if not device_id:
        raise ValueError("device_id required")
    if not name:
        raise ValueError("name required")

    faces = list_device_faces(device_id)
    if not faces:
        raise ValueError("当前画面无人脸，请确保 ESP32 正在推流且已检出脸")

    if face_id is None:
        if len(faces) == 1:
            face_id = next(iter(faces.keys()))
        else:
            raise ValueError(
                f"画面中有 {len(faces)} 张脸，请指定 face_id 或让用户说明是左/右/哪一位"
            )
    else:
        face_id = int(face_id)

    face = faces.get(face_id)
    if not face:
        ids = ", ".join(str(k) for k in sorted(faces.keys()))
        raise ValueError(f"face_id={face_id} 不在当前帧中（可用: {ids}）")

    payload = {**face, **(extra or {}), "device_id": device_id, "face_id": face_id, "name": name}
    desc = resolve_descriptor_from_payload(payload)
    if desc is None:
        raise ValueError("无法提取人脸 embedding/特征，请让人正对镜头、五官清晰后再试")

    cfg = load_camera_face_cfg_file(device_id=device_id) or {}
    merge_thr = float(cfg.get("identity_similarity_threshold", 0.40))
    profiles = load_face_profiles(device_id=device_id)
    profile = upsert_profile(
        profiles,
        name=name,
        descriptor=desc,
        merge_threshold=merge_thr,
    )
    save_face_profiles(profiles, device_id=device_id)
    reload_all_trackers()
    return {
        "ok": True,
        "profile": profile,
        "face_id": face_id,
        "device_id": device_id,
    }
