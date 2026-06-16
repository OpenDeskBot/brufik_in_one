"""设备最近一帧相机 JPEG 缓存（供 ``capture_camera`` 工具）。"""

from __future__ import annotations

import base64
import threading
import time
from typing import Any, Optional

_lock = threading.Lock()
_by_device: dict[str, dict[str, Any]] = {}


def update_device_camera_frame(
    device_id: str,
    jpeg_bytes: bytes,
    *,
    width: int = 0,
    height: int = 0,
    source: str = "uplink",
) -> None:
    dev = str(device_id or "").strip()
    if not dev or not jpeg_bytes:
        return
    with _lock:
        _by_device[dev] = {
            "jpeg": bytes(jpeg_bytes),
            "ts": time.time(),
            "width": int(width or 0),
            "height": int(height or 0),
            "source": str(source or "uplink"),
        }


def get_device_camera_frame(device_id: str) -> Optional[dict[str, Any]]:
    dev = str(device_id or "").strip()
    if not dev:
        return None
    with _lock:
        row = _by_device.get(dev)
        if not row:
            return None
        return {
            "jpeg": bytes(row["jpeg"]),
            "ts": float(row.get("ts") or 0),
            "width": int(row.get("width") or 0),
            "height": int(row.get("height") or 0),
            "source": str(row.get("source") or ""),
        }


def capture_camera_for_device(device_id: str) -> dict[str, Any]:
    """工具 ``capture_camera``：返回最近 JPEG（含 base64）。"""
    row = get_device_camera_frame(device_id)
    if not row:
        return {
            "ok": False,
            "error": "暂无相机帧，请确认 ESP32 已上传 camera_frame 且 cam_fps>0",
        }
    jpeg = row["jpeg"]
    return {
        "ok": True,
        "ts": row["ts"],
        "width": row["width"],
        "height": row["height"],
        "source": row["source"],
        "jpeg_bytes": len(jpeg),
        "jpeg_base64": base64.standard_b64encode(jpeg).decode("ascii"),
    }
