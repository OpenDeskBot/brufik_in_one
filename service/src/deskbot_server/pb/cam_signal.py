"""pb ``cam_fps`` 字段：动态提高相机 JPEG 上行帧率（空 pb_single）。"""

from __future__ import annotations

import uuid
from typing import Any

from deskbot_server.pb.shapes import PB_ACTION_DEFAULT, PB_LEVEL_TASK
from deskbot_server.pb.servo_pcm import parse_pb_cam_fps


def build_cam_fps_signal_pb(
    *,
    cam_fps: int,
    req: str | None = None,
) -> dict[str, Any]:
    """空 pb_single：仅 ``cam_fps``，不含 anim/servo/audio。"""
    fps = parse_pb_cam_fps(cam_fps)
    if fps is None:
        raise ValueError("cam_fps must be a positive integer")
    return {
        "type": "pb_single",
        "req": req or uuid.uuid4().hex[:16],
        "idx": 0,
        "chunk_ms": 1,
        "pb_ver": 2,
        "action": PB_ACTION_DEFAULT,
        "level": PB_LEVEL_TASK,
        "cam_fps": fps,
    }
