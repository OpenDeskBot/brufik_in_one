"""设备最近一帧相机 JPEG 缓存（供 ``capture_camera`` 工具）。"""

from __future__ import annotations

import asyncio
import base64
import logging
import threading
import time
from typing import Any, Optional

logger = logging.getLogger("deskbot-server")

_lock = threading.Lock()
_frame_ready = threading.Condition(_lock)
_by_device: dict[str, dict[str, Any]] = {}

_DEFAULT_CAPTURE_FPS = 5
_DEFAULT_MAX_AGE_S = 1.5
_DEFAULT_WAIT_TIMEOUT_S = 4.0


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
    with _frame_ready:
        _by_device[dev] = {
            "jpeg": bytes(jpeg_bytes),
            "ts": time.time(),
            "width": int(width or 0),
            "height": int(height or 0),
            "source": str(source or "uplink"),
        }
        _frame_ready.notify_all()


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


def wait_for_device_camera_frame(
    device_id: str,
    *,
    after_ts: float = 0.0,
    max_age_s: float = _DEFAULT_MAX_AGE_S,
    timeout: float = _DEFAULT_WAIT_TIMEOUT_S,
) -> Optional[dict[str, Any]]:
    """阻塞等待可用帧：``after_ts`` 之后的新帧，或足够新的缓存帧。"""
    dev = str(device_id or "").strip()
    if not dev:
        return None
    deadline = time.time() + max(0.1, float(timeout))
    with _frame_ready:
        while True:
            row = _by_device.get(dev)
            now = time.time()
            if row:
                ts = float(row.get("ts") or 0)
                fresh_enough = (now - ts) <= max_age_s
                new_enough = ts > float(after_ts) + 1e-6
                if fresh_enough and (after_ts <= 0 or new_enough):
                    return {
                        "jpeg": bytes(row["jpeg"]),
                        "ts": ts,
                        "width": int(row.get("width") or 0),
                        "height": int(row.get("height") or 0),
                        "source": str(row.get("source") or ""),
                    }
            remaining = deadline - now
            if remaining <= 0:
                break
            _frame_ready.wait(timeout=min(0.1, remaining))
    return get_device_camera_frame(device_id)


def _frame_to_capture_result(row: dict[str, Any]) -> dict[str, Any]:
    jpeg = row["jpeg"]
    b64 = base64.standard_b64encode(jpeg).decode("ascii")
    image_display: dict[str, Any] | None = None
    try:
        from deskbot_server.pb.llm_display import decode_llm_image_item

        image_display = decode_llm_image_item({"b64": b64, "x": 0, "y": 0})
    except Exception:
        image_display = None
    out: dict[str, Any] = {
        "ok": True,
        "ts": row["ts"],
        "width": row["width"],
        "height": row["height"],
        "source": row["source"],
        "jpeg_bytes": len(jpeg),
        "jpeg_base64": b64,
    }
    if image_display:
        out["image_display"] = image_display
    return out


def capture_camera_for_device(device_id: str) -> dict[str, Any]:
    """工具 ``capture_camera``：返回最近 JPEG（含 base64）。"""
    row = wait_for_device_camera_frame(
        device_id,
        after_ts=0.0,
        max_age_s=_DEFAULT_MAX_AGE_S,
        timeout=0.0,
    )
    if row is None:
        row = get_device_camera_frame(device_id)
    if not row:
        return {
            "ok": False,
            "error": "暂无相机帧；请确认设备已连接且相机上行已开启（收音期间可能暂停上传）",
        }
    age = time.time() - float(row.get("ts") or 0)
    if age > _DEFAULT_MAX_AGE_S:
        return {
            "ok": False,
            "error": (
                f"相机帧过旧（{age:.1f}s 前）；收音或播报期间设备可能暂停上传，请稍后再试"
            ),
            "ts": row["ts"],
        }
    return _frame_to_capture_result(row)


async def request_camera_uplink_boost(
    device_id: str,
    hub: Any,
    *,
    cam_fps: int = _DEFAULT_CAPTURE_FPS,
) -> None:
    """通过 pb 提示设备提高相机上行帧率。"""
    dev = str(device_id or "").strip()
    if not dev or hub is None:
        return
    try:
        from deskbot_server.pb.cam_signal import build_cam_fps_signal_pb

        payload = build_cam_fps_signal_pb(cam_fps=cam_fps)
        n = await hub.send(dev, payload)
        logger.info(
            "[capture_camera] cam_fps=%d boost device_id=%s delivered=%s",
            cam_fps,
            dev,
            n,
        )
    except Exception as exc:
        logger.warning("[capture_camera] cam_fps boost failed device_id=%s: %s", dev, exc)


async def capture_camera_for_device_async(
    device_id: str,
    *,
    hub: Any = None,
    cam_fps: int = _DEFAULT_CAPTURE_FPS,
    max_age_s: float = _DEFAULT_MAX_AGE_S,
    wait_timeout_s: float = _DEFAULT_WAIT_TIMEOUT_S,
) -> dict[str, Any]:
    """异步拍照：必要时下发 ``cam_fps`` 并等待新帧。"""
    dev = str(device_id or "").strip()
    if not dev:
        return {"ok": False, "error": "缺少 device_id"}

    row = get_device_camera_frame(dev)
    now = time.time()
    if row and (now - float(row.get("ts") or 0)) <= max_age_s:
        return _frame_to_capture_result(row)

    after_ts = now
    await request_camera_uplink_boost(dev, hub, cam_fps=cam_fps)
    row = await asyncio.to_thread(
        wait_for_device_camera_frame,
        dev,
        after_ts=after_ts,
        max_age_s=max_age_s,
        timeout=wait_timeout_s,
    )
    if not row:
        stale = get_device_camera_frame(dev)
        if stale:
            age = time.time() - float(stale.get("ts") or 0)
            return {
                "ok": False,
                "error": (
                    f"等待新相机帧超时（{wait_timeout_s:.1f}s）；"
                    f"最近一帧 {age:.1f}s 前（收音/播报期间设备可能暂停上传）"
                ),
                "ts": stale.get("ts"),
            }
        return {
            "ok": False,
            "error": "暂无相机帧；请确认设备已连接且相机上行已开启",
        }
    return _frame_to_capture_result(row)
