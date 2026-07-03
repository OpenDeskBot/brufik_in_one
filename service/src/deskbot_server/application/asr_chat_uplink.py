"""``/asr_chat`` 上行：``next_bin_len`` + binary（音频 / JPEG）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, Optional

from deskbot_server.application.camera_broker import CameraImageBroker
from deskbot_server.application.camera_jpeg_pipeline import (
    create_camera_face_session,
    process_camera_jpeg_frame,
)
from deskbot_server.application.face_detector import CameraFaceDetector
from deskbot_server.application.face_tracker import FaceTracker
from deskbot_server.vision.undistort import CameraFaceRuntime
from deskbot_server.ws.asr_chat_hub import AsrChatHub
from deskbot_server.ws.device_pipeline import DevicePipelineBroker

logger = logging.getLogger("deskbot-server")

PendingKind = Literal["audio", "camera_frame"]

_MAX_NEXT_BIN_LEN = 512 * 1024


def coerce_next_bin_len(data: dict[str, Any]) -> int:
    """``next_bin_len`` > 0 表示下一条为 binary；兼容旧草案 ``next_bin``+``len``。"""
    raw = data.get("next_bin_len")
    if raw is None and data.get("next_bin") in (1, True, "1"):
        raw = data.get("len")
    try:
        n = int(raw or 0)
    except (TypeError, ValueError):
        return 0
    if n <= 0:
        return 0
    if n > _MAX_NEXT_BIN_LEN:
        logger.warning("[/asr_chat] next_bin_len=%d 超过上限 %d，截断", n, _MAX_NEXT_BIN_LEN)
        return _MAX_NEXT_BIN_LEN
    return n


def coerce_opus_frames(data: dict[str, Any]) -> Optional[int]:
    """Opus batch 帧数；缺省或 1 表示单帧 binary。"""
    raw = data.get("frames")
    if raw is None:
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n > 1 else None


@dataclass
class PendingUplinkBinary:
    kind: PendingKind
    length: int
    codec: Optional[str] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    opus_frames: Optional[int] = None


@dataclass
class AsrChatCameraPipeline:
    """单条 ``/asr_chat`` 连接复用的人脸检测上下文（懒加载）。"""

    runtime: CameraFaceRuntime
    device_id: str
    detector: Optional[CameraFaceDetector] = None
    face_tracker: Optional[FaceTracker] = None
    frame_count: int = 0

    async def ensure_ready(self) -> bool:
        if self.detector is not None:
            return True
        try:
            self.detector, self.face_tracker = await create_camera_face_session(
                self.runtime,
                device_id=self.device_id,
                log_channel="/asr_chat",
            )
            return True
        except Exception as exc:
            logger.error(
                "[/asr_chat] 人脸检测器初始化失败 device_id=%s: %s",
                self.device_id,
                exc,
            )
            return False

    async def process_jpeg(
        self,
        frame_bytes: bytes,
        *,
        image_broker: CameraImageBroker,
        dp_broker: DevicePipelineBroker,
        asr_chat_hub: AsrChatHub,
        send_face_info_to_asr_chat: bool = False,
    ) -> None:
        from deskbot_server.device_camera_frame_store import update_device_camera_frame

        update_device_camera_frame(
            self.device_id,
            frame_bytes,
            width=self.runtime.frame_width,
            height=self.runtime.frame_height,
            source="asr_chat",
        )
        if not await self.ensure_ready():
            return
        assert self.detector is not None and self.face_tracker is not None

        self.frame_count += 1
        result = await process_camera_jpeg_frame(
            device_id=self.device_id,
            frame_bytes=frame_bytes,
            detector=self.detector,
            face_tracker=self.face_tracker,
            runtime=self.runtime,
            image_broker=image_broker,
            dp_broker=dp_broker,
            asr_chat_hub=asr_chat_hub,
            frame_source="asr_chat",
            log_channel="/asr_chat",
            send_face_info_to_asr_chat=send_face_info_to_asr_chat,
        )
        if self.frame_count == 1 or self.frame_count % 30 == 0:
            logger.info(
                "[/asr_chat] camera_frame device_id=%s frame=%d infer_ms=%.1f faces=%s",
                self.device_id,
                self.frame_count,
                result.infer_ms,
                (result.detect or {}).get("face_count") if result.detected else None,
            )
