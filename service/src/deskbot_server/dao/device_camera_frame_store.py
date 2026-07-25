"""相机抓拍工具：经 ``CameraFaceService`` 临时订阅视频流取一帧。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("deskbot-server")

_DEFAULT_CAPTURE_FPS = 5
_DEFAULT_WAIT_TIMEOUT_S = 4.0


async def request_camera_uplink_boost(device_id: str, hub: Any, *, cam_fps: int = _DEFAULT_CAPTURE_FPS) -> None:
    """通过 pb 提示设备提高相机上行帧率。"""
    dev = str(device_id or "").strip()
    if not dev or hub is None:
        return
    try:
        from deskbot_server.pb.cam_signal import build_cam_fps_signal_pb

        payload = build_cam_fps_signal_pb(cam_fps=cam_fps)
        n = await hub.send(dev, payload)
        logger.info("[capture_camera] cam_fps=%d boost device_id=%s delivered=%s", cam_fps, dev, n)
    except Exception as exc:
        logger.warning("[capture_camera] cam_fps boost failed device_id=%s: %s", dev, exc)


async def capture_camera_for_device_async(
    device_id: str,
    *,
    hub: Any = None,
    cam_fps: int = _DEFAULT_CAPTURE_FPS,
    wait_timeout_s: float = _DEFAULT_WAIT_TIMEOUT_S,
) -> dict[str, Any]:
    """异步抓拍：可选提升 cam_fps，临时订阅视频流取一帧后取消订阅。"""
    from deskbot_server.service.camera_face_service import CameraFaceService

    dev = str(device_id or "").strip()
    if not dev:
        return {"ok": False, "error": "缺少 device_id"}

    await request_camera_uplink_boost(dev, hub, cam_fps=cam_fps)
    return await CameraFaceService().capture_frame_async(dev, timeout_s=wait_timeout_s)
