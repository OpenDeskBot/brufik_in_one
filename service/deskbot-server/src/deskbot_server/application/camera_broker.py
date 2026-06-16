"""摄像头 JPEG 帧广播（订阅 /camera_view）。"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Callable, Optional

from deskbot_server.vision.geometry import FACE_FRAME_HEIGHT, FACE_FRAME_WIDTH

WsSendFn = Callable


class CameraImageBroker:
    """JPEG 帧 pub/sub；通过注入的 ``send_fn`` 写出 WebSocket，不绑定 ws 包。"""

    def __init__(self, send_fn: WsSendFn) -> None:
        self._send_fn = send_fn
        self._subscribers: dict = {}
        self._last_by_device: dict = {}
        self._inflight: dict = {}
        self._lock = asyncio.Lock()

    async def _send_pair(self, ws, meta_json: str, frame: bytes) -> None:
        try:
            await self._send_fn(ws, meta_json)
            await self._send_fn(ws, frame)
        except Exception:
            pass

    async def add_subscriber(self, ws, device_filter: Optional[str] = None) -> None:
        async with self._lock:
            self._subscribers[ws] = device_filter
            if device_filter:
                snap = self._last_by_device.get(device_filter)
                items = [(device_filter, snap)] if snap else []
            else:
                items = list(self._last_by_device.items())
        for _device_id, payload in items:
            if not payload:
                continue
            meta_json, frame = payload
            prev = self._inflight.get(ws)
            if prev is not None and not prev.done():

                async def _chain(prev_task, ws=ws, mj=meta_json, fr=frame):
                    try:
                        await prev_task
                    except Exception:
                        pass
                    await self._send_pair(ws, mj, fr)

                self._inflight[ws] = asyncio.create_task(_chain(prev))
            else:
                self._inflight[ws] = asyncio.create_task(
                    self._send_pair(ws, meta_json, frame)
                )

    async def remove_subscriber(self, ws) -> None:
        async with self._lock:
            self._subscribers.pop(ws, None)
        task = self._inflight.pop(ws, None)
        if task is not None and not task.done():
            task.cancel()

    async def publish(
        self,
        device_id: str,
        frame: bytes,
        *,
        detected: Optional[bool] = None,
        landmarks: Optional[list] = None,
        frame_w: int = FACE_FRAME_WIDTH,
        frame_h: int = FACE_FRAME_HEIGHT,
        yaw_deg: Optional[float] = None,
        pitch_deg: Optional[float] = None,
        iris_offsets: Optional[dict] = None,
        face_score: Optional[float] = None,
        frontal_score: Optional[float] = None,
        is_frontal: Optional[bool] = None,
        confidence: Optional[float] = None,
        points: Optional[list] = None,
        faces: Optional[list] = None,
        face_count: Optional[int] = None,
        face_id: Optional[int] = None,
    ) -> tuple:
        if not frame:
            return 0, 0
        device_id = str(device_id or "unknown")
        meta = {
            "type": "camera_frame",
            "device_id": device_id,
            "size": len(frame),
            "ts": time.time(),
            "t_mono": time.monotonic(),
            "frame_w": int(frame_w),
            "frame_h": int(frame_h),
        }
        if detected is not None:
            meta["detected"] = bool(detected)
        if landmarks:
            meta["landmarks"] = landmarks
        if yaw_deg is not None:
            try:
                meta["yaw_deg"] = float(yaw_deg)
            except (TypeError, ValueError):
                pass
        if pitch_deg is not None:
            try:
                meta["pitch_deg"] = float(pitch_deg)
            except (TypeError, ValueError):
                pass
        if iris_offsets:
            sanitized = {}
            for k, v in iris_offsets.items():
                if v is None:
                    continue
                try:
                    sanitized[str(k)] = float(v)
                except (TypeError, ValueError):
                    continue
            if sanitized:
                meta["iris_offsets"] = sanitized
        if face_score is not None:
            try:
                meta["face_score"] = float(face_score)
            except (TypeError, ValueError):
                pass
        if frontal_score is not None:
            try:
                meta["frontal_score"] = float(frontal_score)
            except (TypeError, ValueError):
                pass
        if is_frontal is not None:
            meta["is_frontal"] = bool(is_frontal)
        if confidence is not None:
            try:
                meta["confidence"] = float(confidence)
            except (TypeError, ValueError):
                pass
        if points:
            meta["points"] = points
        if face_count is not None:
            try:
                meta["face_count"] = int(face_count)
            except (TypeError, ValueError):
                pass
        if face_id is not None:
            try:
                meta["face_id"] = int(face_id)
            except (TypeError, ValueError):
                pass
        if faces:
            meta["faces"] = faces
        meta_json = json.dumps(meta, ensure_ascii=False)
        async with self._lock:
            self._last_by_device[device_id] = (meta_json, frame)
            targets = [
                ws for ws, flt in self._subscribers.items() if not flt or flt == device_id
            ]
        attempted = len(targets)
        if not targets:
            return 0, 0
        sent = 0
        for ws in targets:
            prev = self._inflight.get(ws)
            if prev is not None and not prev.done():
                continue
            self._inflight[ws] = asyncio.create_task(self._send_pair(ws, meta_json, frame))
            sent += 1
        return sent, attempted
