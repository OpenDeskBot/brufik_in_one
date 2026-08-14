"""人脸分析 → 舵机角度换算与持续人脸跟随。"""

from __future__ import annotations

import math
import time
import uuid
from typing import TYPE_CHECKING, Any

from deskbot_server.dao.debug_prefs_store import get_camera_servo_auto_mode
from deskbot_server.dao.servo_config_store import clamp_servo_step, servo_limits
from deskbot_server.pb.servo_pcm import attach_pb_device_hints_from_config
from deskbot_server.pb.shapes import PB_ACTION_REPLACE, PB_LEVEL_IDLE
from deskbot_server.dao.debug_prefs_store import get_auto_reply
from deskbot_server.vision.camera_face_tune import (
    get_frontal_angle_threshold_deg,
    get_gaze_pitch_threshold_deg,
    get_gaze_yaw_threshold_deg,
    get_horizontal_fov_deg,
)
from deskbot_server.vision.geometry import FRONTAL_YAW_THRESHOLD_DEG

if TYPE_CHECKING:
    from deskbot_server.ws.asr_chat_hub import AsrChatHub

_SERVO_CENTER_X = 90
_SERVO_CENTER_Y = 90
# 摄像头画面坐标相对舵机逻辑坐标需要反向；clamp_servo_step 再应用设备的 xReverse 校准。
_MAP_YAW_SIGN = -1
_MAP_PITCH_SIGN = 1
_FOLLOW_PITCH_OFFSET = -15
_GAZE_PITCH_OFFSET = -15
_SERVO_MS = 200
_FOLLOW_MIN_GAP_MS = 220

_device_state: dict[str, dict[str, float | int]] = {}


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _screen_angles_from_analysis(analysis: dict[str, Any]) -> tuple[float | None, float | None]:
    landmarks = analysis.get("landmarks") or []
    nose = next((p for p in landmarks if isinstance(p, dict) and p.get("name") == "nose"), None)
    w = int(analysis.get("image_w") or 0)
    h = int(analysis.get("image_h") or 0)
    if not nose or w <= 0 or h <= 0:
        return None, None
    try:
        nx = float(nose["x"])
        ny = float(nose["y"])
    except (TypeError, ValueError, KeyError):
        return None, None

    hfov_rad = math.radians(get_horizontal_fov_deg())
    vfov_rad = 2 * math.atan(math.tan(hfov_rad / 2) * (h / w))
    dx = nx - w / 2
    dy = ny - h / 2
    r2d = 180 / math.pi
    screen_yaw = math.atan((2 * dx * math.tan(hfov_rad / 2)) / w) * r2d
    screen_pitch = math.atan((2 * dy * math.tan(vfov_rad / 2)) / h) * r2d
    return round(screen_yaw, 1), round(screen_pitch, 1)


def _mode_accepts_face(mode: str, analysis: dict[str, Any]) -> bool:
    if not analysis.get("landmarks"):
        return False
    if mode == "follow":
        return True
    if mode == "follow_frontal":
        frontal = analysis.get("is_frontal_angle")
        if isinstance(frontal, bool):
            return frontal
        try:
            return float(analysis.get("frontal_angle_deg")) <= get_frontal_angle_threshold_deg(
                FRONTAL_YAW_THRESHOLD_DEG
            )
        except (TypeError, ValueError):
            return False
    if mode == "gaze":
        looking = analysis.get("is_looking_at_camera")
        if isinstance(looking, bool):
            return looking
        try:
            return abs(float(analysis.get("gaze_yaw_deg"))) < get_gaze_yaw_threshold_deg(
                FRONTAL_YAW_THRESHOLD_DEG
            ) and abs(float(analysis.get("gaze_pitch_deg"))) < get_gaze_pitch_threshold_deg(FRONTAL_YAW_THRESHOLD_DEG)
        except (TypeError, ValueError):
            return False
    return False


async def camera_servo_follower_tick(hub: AsrChatHub, device_id: str, analysis: dict[str, Any]) -> None:
    """按当前跟随模式下发最新的人脸绝对位置，避免重复或过密的舵机命令。"""
    if not get_auto_reply(device_id):
        return
    dev = str(device_id or "").strip()
    mode = get_camera_servo_auto_mode(device_id)
    if not dev or mode not in ("follow", "follow_frontal", "gaze") or not _mode_accepts_face(mode, analysis):
        return

    yaw, pitch = _screen_angles_from_analysis(analysis)
    if yaw is None or pitch is None:
        return
    dead_zone = 0.5 if mode == "gaze" else 0.15
    if abs(yaw) <= dead_zone and abs(pitch) <= dead_zone:
        return

    limits = servo_limits(device_id=dev)
    target_x = int(round(_clamp(_SERVO_CENTER_X + _MAP_YAW_SIGN * yaw, limits["xMin"], limits["xMax"])))
    target_y = int(
        round(_clamp(_SERVO_CENTER_Y + _MAP_PITCH_SIGN * pitch + _FOLLOW_PITCH_OFFSET, limits["yMin"], limits["yMax"]))
    )
    step = clamp_servo_step(
        {"xm": 0, "ym": 0, "x": target_x, "y": target_y, "ms": _SERVO_MS}, device_id=dev, limits=limits
    )

    now_ms = time.monotonic() * 1000
    state = _device_state.setdefault(dev, {})
    last_send = float(state.get("last_send_ms") or 0)
    if now_ms - last_send < _FOLLOW_MIN_GAP_MS:
        return
    if state.get("x") == step["x"] and state.get("y") == step["y"] and now_ms - last_send < 1600:
        return

    request_id = uuid.uuid4().hex[:16]
    payload: dict[str, Any] = {
        "type": "pb_single",
        "req": request_id,
        "idx": 0,
        "chunk_ms": _SERVO_MS,
        "pb_ver": 2,
        "action": PB_ACTION_REPLACE,
        # idle：低于口播(level=1)；设备端纯舵机叠层，不会取消正在播放的音频。
        "level": PB_LEVEL_IDLE,
        "servo": [step],
    }
    attach_pb_device_hints_from_config(payload)
    delivered = await hub.send(dev, payload)
    if delivered <= 0:
        return

    state.update(last_send_ms=now_ms, x=step["x"], y=step["y"])
    from deskbot_server.ws.device_pipeline import publish_auto_dispatch_event

    await publish_auto_dispatch_event(
        hub.pipeline_broker,
        device_id=dev,
        request_id=request_id,
        source="auto_face_follow",
        summary=f"{mode} 舵机 ({step['x']}, {step['y']})",
        status="ok",
    )
