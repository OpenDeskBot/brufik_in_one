"""摄像头 JPEG 帧：检测、跟踪、broker 与舵机跟随（经 /asr_chat camera_frame 上行）。"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Literal, Optional

from deskbot_server.application.camera_broker import CameraImageBroker
from deskbot_server.application.camera_frame import (
    analyze_face_detections,
    build_face_info_message,
    build_face_pos_payload,
)
from deskbot_server.application.camera_servo_follower import camera_servo_follower_tick
from deskbot_server.application.face_detector import CameraFaceDetector
from deskbot_server.application.face_tracker import FaceTracker
from deskbot_server.core.concurrency import face_infer_slot
from deskbot_server.face_identity import attach_descriptors_to_faces, deduplicate_overlapping_faces
from deskbot_server.face_snapshot_cache import update_device_faces
from deskbot_server.vision.undistort import CameraFaceRuntime
from deskbot_server.ws.asr_chat_hub import AsrChatHub
from deskbot_server.ws.device_pipeline import DevicePipelineBroker

logger = logging.getLogger("deskbot-server")

FrameSource = Literal["asr_chat"]


@dataclass
class CameraJpegProcessResult:
    infer_ms: float
    detected: bool
    detect: Optional[dict] = None
    view_sent: int = 0
    view_attempted: int = 0


async def create_camera_face_session(
    runtime: CameraFaceRuntime,
    *,
    device_id: str,
    log_channel: str,
) -> tuple[CameraFaceDetector, FaceTracker]:
    """创建单连接级 detector + tracker，并按需预加载 InsightFace。"""
    detector = await asyncio.to_thread(
        CameraFaceDetector,
        num_faces=runtime.num_faces,
        undistorter=runtime.undistorter,
        min_face_detection_confidence=runtime.min_face_detection_confidence,
        min_face_presence_confidence=runtime.min_face_presence_confidence,
        frame_width=runtime.frame_width,
        frame_height=runtime.frame_height,
    )
    face_tracker = FaceTracker(
        device_id=device_id,
        max_dist_px=runtime.face_track_max_dist_px,
        max_lost_frames=runtime.face_track_max_lost_frames,
        identity_similarity_threshold=runtime.identity_similarity_threshold,
        identity_geometry_threshold=runtime.identity_geometry_threshold,
    )
    if runtime.face_embedding_enabled:
        try:
            from deskbot_server.vision.face_embedding import get_face_embedding_engine

            await asyncio.to_thread(get_face_embedding_engine)
        except Exception as exc:
            logger.warning(
                "[%s] InsightFace 预加载失败 device_id=%s: %s",
                log_channel,
                device_id,
                exc,
            )
    return detector, face_tracker


async def process_camera_jpeg_frame(
    *,
    device_id: str,
    frame_bytes: bytes,
    detector: CameraFaceDetector,
    face_tracker: FaceTracker,
    runtime: CameraFaceRuntime,
    image_broker: CameraImageBroker,
    dp_broker: DevicePipelineBroker,
    asr_chat_hub: AsrChatHub,
    frame_source: FrameSource,
    log_channel: str,
    send_face_info_to_asr_chat: bool = False,
) -> CameraJpegProcessResult:
    """单帧 JPEG：检测 → 缓存 → broker / device_pipeline / 舵机跟随。"""
    from deskbot_server.device_camera_frame_store import update_device_camera_frame

    update_device_camera_frame(
        device_id,
        frame_bytes,
        width=runtime.frame_width,
        height=runtime.frame_height,
        source=frame_source,
    )

    t0 = time.monotonic()
    try:
        async with face_infer_slot():
            raw_faces = await asyncio.to_thread(detector.detect_faces, frame_bytes)
            raw_faces = deduplicate_overlapping_faces(raw_faces)
            await asyncio.to_thread(
                attach_descriptors_to_faces,
                raw_faces,
                bgr_image=detector.last_bgr,
            )
            tagged_faces = face_tracker.assign_ids(raw_faces)
        update_device_faces(device_id, tagged_faces)
        detect = analyze_face_detections(tagged_faces)
    except Exception as exc:
        infer_ms = (time.monotonic() - t0) * 1000.0
        logger.warning(
            "[%s] 推理失败 device_id=%s: %s",
            log_channel,
            device_id,
            exc,
        )
        _s, _a = await image_broker.publish(device_id, frame_bytes, detected=False)
        _tick_pb_idle_gaze(asr_chat_hub, device_id, False)
        return CameraJpegProcessResult(
            infer_ms=infer_ms,
            detected=False,
            view_sent=_s,
            view_attempted=_a,
        )

    infer_ms = (time.monotonic() - t0) * 1000.0
    if not detect or not detect.get("points"):
        _s, _a = await image_broker.publish(device_id, frame_bytes, detected=False)
        _tick_pb_idle_gaze(asr_chat_hub, device_id, False)
        return CameraJpegProcessResult(
            infer_ms=infer_ms,
            detected=False,
            view_sent=_s,
            view_attempted=_a,
        )

    analysis = detect
    _tick_pb_idle_gaze(asr_chat_hub, device_id, analysis["is_frontal"])

    await dp_broker.broadcast_to_device(
        device_id, build_face_pos_payload(device_id, analysis)
    )
    view_sent, view_attempted = await image_broker.publish(
        device_id,
        frame_bytes,
        detected=True,
        landmarks=analysis["landmarks"],
        frame_w=analysis["image_w"],
        frame_h=analysis["image_h"],
        yaw_deg=analysis["yaw_deg"],
        pitch_deg=analysis["pitch_deg"],
        iris_offsets=analysis["iris_offsets"],
        face_score=analysis.get("face_score"),
        frontal_score=analysis["frontal_score"],
        is_frontal=analysis["is_frontal"],
        confidence=analysis.get("face_score"),
        points=analysis["points"],
        faces=analysis.get("faces"),
        face_count=analysis.get("face_count"),
        face_id=analysis.get("face_id"),
    )

    face_info = build_face_info_message(
        device_id, analysis, send_face_info=send_face_info_to_asr_chat
    )
    if face_info is not None:
        await asr_chat_hub.send(device_id, face_info)

    await camera_servo_follower_tick(asr_chat_hub, device_id, analysis)
    return CameraJpegProcessResult(
        infer_ms=infer_ms,
        detected=True,
        detect=analysis,
        view_sent=view_sent,
        view_attempted=view_attempted,
    )


def _tick_pb_idle_gaze(asr_chat_hub: AsrChatHub, device_id: str, is_frontal: bool) -> None:
    pb_idle = getattr(asr_chat_hub, "pb_idle_snore", None)
    if pb_idle is not None:
        pb_idle.on_camera_gaze_tick(device_id, is_frontal)
