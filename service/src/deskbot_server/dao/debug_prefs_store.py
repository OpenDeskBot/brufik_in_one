"""调试页偏好：自动应答、人脸跟随模式等，存储在 devices 表。"""

from __future__ import annotations

from typing import Any

from deskbot_server.dao import device_mapper

_VALID_SERVO_AUTO_MODES = frozenset({"", "follow", "follow_frontal", "gaze"})

# live_service 是全局开关，保留内存态
_live_service_enabled: bool = True


def normalize_camera_servo_auto_mode(raw: object) -> str:
    mode = str(raw or "").strip()
    return mode if mode in _VALID_SERVO_AUTO_MODES else ""


# ---- 设备级开关（读写 devices 表）----


def get_auto_reply(device_id: str) -> bool:
    dev = device_mapper.get_by_device_id(device_id)
    return bool(dev.auto_reply) if dev else True


def set_auto_reply(device_id: str, enabled: bool) -> None:
    device_mapper.update_auto_reply(device_id, bool(enabled))
    if not enabled:
        device_mapper.update_servo_mode(device_id, "")


def get_camera_servo_auto_mode(device_id: str) -> str:
    dev = device_mapper.get_by_device_id(device_id)
    if dev is None:
        return ""
    return normalize_camera_servo_auto_mode(dev.servo_mode)


def set_camera_servo_auto_mode(device_id: str, mode: object) -> str:
    norm = normalize_camera_servo_auto_mode(mode)
    device_mapper.update_servo_mode(device_id, norm)
    return norm


# ---- 全局开关（内存态）----


def get_live_service_enabled() -> bool:
    return _live_service_enabled


def set_live_service_enabled(enabled: bool) -> None:
    global _live_service_enabled
    _live_service_enabled = bool(enabled)


# ---- 快照（供调试页读取）----


def debug_prefs_snapshot(device_id: str) -> dict[str, Any]:
    return {
        "asr_auto_reply": get_auto_reply(device_id),
        "live_service": get_live_service_enabled(),
        "camera_servo_auto_mode": get_camera_servo_auto_mode(device_id),
    }
