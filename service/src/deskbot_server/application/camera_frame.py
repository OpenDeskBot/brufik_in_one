"""摄像头帧处理：几何量计算与 face_info 组装（无 WebSocket）。"""

from __future__ import annotations

import time
from typing import Any, Optional

from deskbot_server.vision.geometry import (
    FACE_FRAME_HEIGHT,
    FACE_FRAME_WIDTH,
    FRONTAL_THRESHOLD,
    FRONTAL_YAW_THRESHOLD_DEG,
    compute_eye_iris_offsets,
    compute_face_pitch_deg,
    compute_face_yaw_deg,
    compute_face_score,
    compute_frontal_angle_deg,
    compute_frontal_score,
    compute_gaze_angles,
    compute_is_frontal_by_angle,
    compute_is_looking_at_camera,
    decompose_facial_transform_matrix,
)
from deskbot_server.camera_face_tune import (
    get_eye_yaw_range_deg,
    get_frontal_angle_threshold_deg,
    get_frontal_threshold,
    get_gaze_pitch_threshold_deg,
    get_gaze_yaw_threshold_deg,
)


def _resolve_head_pose(
    landmarks: list,
    facial_transform: list | None,
) -> tuple[Optional[float], Optional[float], Optional[float], str]:
    """优先 MediaPipe 4×4 变换矩阵，否则回退 9 点 2D 几何。"""
    if facial_transform:
        pose = decompose_facial_transform_matrix(facial_transform)
        if pose is not None:
            return (
                pose.get("yaw_deg"),
                pose.get("pitch_deg"),
                pose.get("roll_deg"),
                "matrix",
            )
    return (
        compute_face_yaw_deg(landmarks),
        compute_face_pitch_deg(landmarks),
        None,
        "landmarks",
    )


def analyze_face_detection(detect: dict) -> dict[str, Any]:
    """从单张人脸原始检出结果计算面向角、正脸分等。"""
    points = detect.get("points") or []
    landmarks = detect.get("landmarks") or []
    image_w = int(detect.get("image_w") or 0) or FACE_FRAME_WIDTH
    image_h = int(detect.get("image_h") or 0) or FACE_FRAME_HEIGHT
    frontal_score = compute_frontal_score(points)
    is_frontal = frontal_score >= get_frontal_threshold(FRONTAL_THRESHOLD)
    face_score = compute_face_score(points, landmarks, image_w=image_w, image_h=image_h)
    yaw_deg, pitch_deg, roll_deg, pose_source = _resolve_head_pose(
        landmarks, detect.get("facial_transform")
    )
    iris_offsets = compute_eye_iris_offsets(landmarks)
    gaze = compute_gaze_angles(
        yaw_deg,
        pitch_deg,
        iris_offsets,
        eye_yaw_range_deg=get_eye_yaw_range_deg(),
    )
    is_looking_at_camera = compute_is_looking_at_camera(
        gaze.get("gaze_yaw_deg"),
        gaze.get("gaze_pitch_deg"),
        yaw_threshold_deg=get_gaze_yaw_threshold_deg(FRONTAL_YAW_THRESHOLD_DEG),
        pitch_threshold_deg=get_gaze_pitch_threshold_deg(FRONTAL_YAW_THRESHOLD_DEG),
    )
    frontal_angle_deg = compute_frontal_angle_deg(yaw_deg, pitch_deg)
    is_frontal_angle = compute_is_frontal_by_angle(
        yaw_deg,
        pitch_deg,
        threshold_deg=get_frontal_angle_threshold_deg(FRONTAL_YAW_THRESHOLD_DEG),
    )
    out: dict[str, Any] = {
        "points": points,
        "landmarks": landmarks,
        "face_score": face_score,
        "frontal_score": frontal_score,
        "is_frontal": is_frontal,
        "frontal_angle_deg": frontal_angle_deg,
        "is_frontal_angle": is_frontal_angle,
        "yaw_deg": yaw_deg,
        "pitch_deg": pitch_deg,
        "roll_deg": roll_deg,
        "pose_source": pose_source,
        "iris_offsets": iris_offsets,
        "eye_yaw_offset_deg": gaze.get("eye_yaw_offset_deg"),
        "gaze_yaw_deg": gaze.get("gaze_yaw_deg"),
        "gaze_pitch_deg": gaze.get("gaze_pitch_deg"),
        "is_looking_at_camera": is_looking_at_camera,
        "image_w": image_w,
        "image_h": image_h,
    }
    face_id = detect.get("face_id")
    if face_id is not None:
        out["face_id"] = int(face_id)
    person_id = detect.get("person_id")
    if person_id is not None:
        out["person_id"] = int(person_id)
    person_name = detect.get("person_name")
    if person_name:
        out["person_name"] = str(person_name)
    identity_score = detect.get("identity_score")
    if identity_score is not None:
        try:
            out["identity_score"] = round(float(identity_score), 3)
        except (TypeError, ValueError):
            pass
    match_source = detect.get("match_source") or detect.get("face_id_source")
    if match_source:
        out["id_match_source"] = str(match_source)
        out["match_source"] = str(match_source)
    dk = detect.get("descriptor_kind")
    if dk:
        out["descriptor_kind"] = str(dk)
    dd = detect.get("descriptor_dim")
    if dd is not None:
        try:
            out["descriptor_dim"] = int(dd)
        except (TypeError, ValueError):
            pass
    return out


def pick_primary_face(
    analyses: list[dict[str, Any]],
    *,
    prefer_frontal_angle: bool = False,
    prefer_gazing: bool = False,
) -> Optional[dict[str, Any]]:
    """多张脸时选主脸。

    - 默认：优先 ``is_frontal``（分数口径），否则 ``frontal_score`` 最高
    - ``prefer_frontal_angle``：优先 ``is_frontal_angle``（角度口径）
    - ``prefer_gazing``：优先 ``is_looking_at_camera``
    """
    if not analyses:
        return None
    pool = analyses
    if prefer_gazing:
        gazing = [a for a in analyses if a.get("is_looking_at_camera")]
        if gazing:
            pool = gazing
    elif prefer_frontal_angle:
        frontal_a = [a for a in analyses if a.get("is_frontal_angle")]
        if frontal_a:
            pool = frontal_a
    else:
        frontal = [a for a in analyses if a.get("is_frontal")]
        if frontal:
            pool = frontal
    return max(pool, key=lambda a: float(a.get("frontal_score") or 0.0))


def analyze_face_detections(
    faces: list[dict],
    *,
    prefer_frontal_angle: bool = False,
    prefer_gazing: bool = False,
) -> dict[str, Any]:
    """多人脸：逐脸分析并附带 ``faces`` 列表；顶层字段来自主脸（兼容旧协议）。"""
    analyses = [analyze_face_detection(face) for face in (faces or [])]
    analyses = [a for a in analyses if a.get("points")]
    primary = pick_primary_face(
        analyses,
        prefer_frontal_angle=prefer_frontal_angle,
        prefer_gazing=prefer_gazing,
    )
    if primary is None:
        return {
            "points": [],
            "landmarks": [],
            "face_score": 0.0,
            "frontal_score": 0.0,
            "is_frontal": False,
            "frontal_angle_deg": None,
            "is_frontal_angle": False,
            "yaw_deg": None,
            "pitch_deg": None,
            "roll_deg": None,
            "pose_source": None,
            "iris_offsets": {"left_eye": None, "right_eye": None},
            "eye_yaw_offset_deg": None,
            "gaze_yaw_deg": None,
            "gaze_pitch_deg": None,
            "is_looking_at_camera": None,
            "image_w": FACE_FRAME_WIDTH,
            "image_h": FACE_FRAME_HEIGHT,
            "faces": [],
            "face_count": 0,
        }
    merged = dict(primary)
    merged["faces"] = analyses
    merged["face_count"] = len(analyses)
    return merged


def build_face_pos_payload(device_id: str, analysis: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    mono = time.monotonic()
    payload: dict[str, Any] = {
        "type": "face_pos",
        "device_id": device_id,
        "points": analysis["points"],
        "width": analysis["image_w"],
        "height": analysis["image_h"],
        "frontal_score": analysis["frontal_score"],
        "is_frontal": analysis["is_frontal"],
        "ts": now,
        "t_mono": mono,
        "source": "asr_chat",
        "image_w": analysis["image_w"],
        "image_h": analysis["image_h"],
    }
    faces = analysis.get("faces") or []
    if faces:
        payload["face_count"] = int(analysis.get("face_count") or len(faces))
        payload["faces"] = [
            {
                "face_id": f.get("face_id"),
                "points": f.get("points") or [],
                "landmarks": f.get("landmarks") or [],
                "face_score": f.get("face_score"),
                "frontal_score": f.get("frontal_score"),
                "is_frontal": f.get("is_frontal"),
                "frontal_angle_deg": f.get("frontal_angle_deg"),
                "is_frontal_angle": f.get("is_frontal_angle"),
                "person_id": f.get("person_id"),
                "person_name": f.get("person_name"),
                "identity_score": f.get("identity_score"),
                "match_source": f.get("match_source") or f.get("face_id_source"),
                "descriptor_kind": f.get("descriptor_kind"),
                "descriptor_dim": f.get("descriptor_dim"),
                "yaw_deg": f.get("yaw_deg"),
                "pitch_deg": f.get("pitch_deg"),
                "roll_deg": f.get("roll_deg"),
                "pose_source": f.get("pose_source"),
                "iris_offsets": f.get("iris_offsets"),
                "eye_yaw_offset_deg": f.get("eye_yaw_offset_deg"),
                "gaze_yaw_deg": f.get("gaze_yaw_deg"),
                "gaze_pitch_deg": f.get("gaze_pitch_deg"),
                "is_looking_at_camera": f.get("is_looking_at_camera"),
            }
            for f in faces
            if isinstance(f, dict)
        ]
        if analysis.get("face_id") is not None:
            payload["face_id"] = analysis["face_id"]
    return payload


def build_face_info_message(
    device_id: str,
    analysis: dict[str, Any],
    *,
    send_face_info: bool,
) -> Optional[dict[str, Any]]:
    if not send_face_info:
        return None
    yaw_deg = analysis.get("yaw_deg")
    if yaw_deg is None:
        return None
    pitch_deg = analysis.get("pitch_deg")
    is_frontal_dir = (
        abs(yaw_deg) < FRONTAL_YAW_THRESHOLD_DEG
        and (pitch_deg is None or abs(pitch_deg) < FRONTAL_YAW_THRESHOLD_DEG)
    )
    now = time.time()
    mono = time.monotonic()
    face_info: dict[str, Any] = {
        "type": "face_info",
        "device_id": device_id,
        "action": "append",
        "yaw_deg": yaw_deg,
        "is_frontal": is_frontal_dir,
        "ts": now,
        "t_mono": mono,
    }
    if pitch_deg is not None:
        face_info["pitch_deg"] = pitch_deg
    landmarks = analysis.get("landmarks") or []
    nose_pt = next(
        (p for p in landmarks if isinstance(p, dict) and p.get("name") == "nose"),
        None,
    )
    if nose_pt is not None:
        try:
            face_info["nose"] = {
                "x": round(float(nose_pt["x"]), 2),
                "y": round(float(nose_pt["y"]), 2),
            }
            face_info["frame_w"] = analysis["image_w"]
            face_info["frame_h"] = analysis["image_h"]
        except (TypeError, ValueError, KeyError):
            pass
    return face_info
