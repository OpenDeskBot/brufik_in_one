"""摄像头人脸检测可调参数（调试页写入，推理路径读取）。"""
from __future__ import annotations

import threading
from typing import Optional

_lock = threading.Lock()
_frontal_threshold: Optional[float] = None
_gaze_yaw_threshold_deg: Optional[float] = None
_gaze_pitch_threshold_deg: Optional[float] = None
_eye_yaw_range_deg: Optional[float] = None
_horizontal_fov_deg: Optional[float] = None
_frontal_angle_threshold_deg: Optional[float] = None
_identity_similarity_threshold: Optional[float] = None
_face_embedding_enabled: Optional[bool] = None


def get_frontal_threshold(default: float = 0.4) -> float:
    with _lock:
        if _frontal_threshold is not None:
            return float(_frontal_threshold)
        return float(default)


def set_frontal_threshold(value: Optional[float]) -> None:
    global _frontal_threshold
    with _lock:
        _frontal_threshold = None if value is None else float(value)


def get_gaze_yaw_threshold_deg(default: float = 15.0) -> float:
    with _lock:
        if _gaze_yaw_threshold_deg is not None:
            return float(_gaze_yaw_threshold_deg)
        return float(default)


def get_gaze_pitch_threshold_deg(default: float = 15.0) -> float:
    with _lock:
        if _gaze_pitch_threshold_deg is not None:
            return float(_gaze_pitch_threshold_deg)
        return float(default)


def get_eye_yaw_range_deg(default: float = 50.0) -> float:
    with _lock:
        if _eye_yaw_range_deg is not None:
            return float(_eye_yaw_range_deg)
        return float(default)


def get_horizontal_fov_deg(default: float = 120.0) -> float:
    with _lock:
        if _horizontal_fov_deg is not None:
            return float(_horizontal_fov_deg)
        return float(default)


def apply_camera_face_tune(cfg: dict) -> None:
    """从 ``camera_face.json`` 合并配置热更新运行时阈值。"""
    set_frontal_threshold(float(cfg.get("frontal_threshold", 0.4)))
    set_gaze_yaw_threshold_deg(float(cfg.get("gaze_yaw_threshold_deg", 15.0)))
    set_gaze_pitch_threshold_deg(float(cfg.get("gaze_pitch_threshold_deg", 15.0)))
    set_eye_yaw_range_deg(float(cfg.get("eye_yaw_range_deg", 50.0)))
    set_horizontal_fov_deg(float(cfg.get("horizontal_fov_deg", 120.0)))
    set_frontal_angle_threshold_deg(float(cfg.get("frontal_angle_threshold_deg", 15.0)))
    fe = cfg.get("face_embedding_enabled")
    if fe is None:
        set_face_embedding_enabled(True)
    else:
        set_face_embedding_enabled(bool(fe))
    ist_default = 0.40 if get_face_embedding_enabled() else 0.82
    set_identity_similarity_threshold(float(cfg.get("identity_similarity_threshold", ist_default)))


def set_gaze_yaw_threshold_deg(value: Optional[float]) -> None:
    global _gaze_yaw_threshold_deg
    with _lock:
        _gaze_yaw_threshold_deg = None if value is None else float(value)


def set_gaze_pitch_threshold_deg(value: Optional[float]) -> None:
    global _gaze_pitch_threshold_deg
    with _lock:
        _gaze_pitch_threshold_deg = None if value is None else float(value)


def set_eye_yaw_range_deg(value: Optional[float]) -> None:
    global _eye_yaw_range_deg
    with _lock:
        _eye_yaw_range_deg = None if value is None else float(value)


def set_horizontal_fov_deg(value: Optional[float]) -> None:
    global _horizontal_fov_deg
    with _lock:
        _horizontal_fov_deg = None if value is None else float(value)


def get_frontal_angle_threshold_deg(default: float = 15.0) -> float:
    with _lock:
        if _frontal_angle_threshold_deg is not None:
            return float(_frontal_angle_threshold_deg)
        return float(default)


def get_identity_similarity_threshold(default: float = 0.88) -> float:
    with _lock:
        if _identity_similarity_threshold is not None:
            return float(_identity_similarity_threshold)
        return float(default)


def set_frontal_angle_threshold_deg(value: Optional[float]) -> None:
    global _frontal_angle_threshold_deg
    with _lock:
        _frontal_angle_threshold_deg = None if value is None else float(value)


def set_identity_similarity_threshold(value: Optional[float]) -> None:
    global _identity_similarity_threshold
    with _lock:
        _identity_similarity_threshold = None if value is None else float(value)


def get_face_embedding_enabled(default: bool = True) -> bool:
    with _lock:
        if _face_embedding_enabled is not None:
            return bool(_face_embedding_enabled)
        return bool(default)


def set_face_embedding_enabled(value: Optional[bool]) -> None:
    global _face_embedding_enabled
    with _lock:
        _face_embedding_enabled = None if value is None else bool(value)
