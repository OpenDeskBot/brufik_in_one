"""独立 /camera_uplink WebSocket：仅接收 camera_frame base64 JSON，与 /asr_chat 分离。"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Optional

from websockets.exceptions import ConnectionClosed

from deskbot_server.application.asr_chat_uplink import AsrChatCameraPipeline
from deskbot_server.application.camera_broker import CameraImageBroker
from deskbot_server.util import _json_msg, _peer_str
from deskbot_server.vision.undistort import CameraFaceRuntime, build_camera_face_runtime
from deskbot_server.config import load_config
from deskbot_server.ws.api_key_gate import record_turn_usage
from deskbot_server.ws.asr_chat import _schedule_camera_jpeg
from deskbot_server.ws.asr_chat_hub import AsrChatHub
from deskbot_server.ws.device_pipeline import DevicePipelineBroker
from deskbot_server.ws.registry import DeviceRegistry
from deskbot_server.ws.ws_send import _safe_send

logger = logging.getLogger("deskbot-server")

_active_camera_uplink: dict[str, object] = {}
_cam_bin_buf: dict[str, bytearray] = {}


def _try_extract_jpeg(device_id: str) -> Optional[bytes]:
    buf = _cam_bin_buf.get(device_id)
    if not buf or len(buf) < 4:
        return None
    if not (buf[0] == 0xFF and buf[1] == 0xD8):
        start = buf.find(b"\xff\xd8")
        if start < 0:
            if len(buf) > 65536:
                buf.clear()
            return None
        del buf[:start]
    end = buf.rfind(b"\xff\xd9")
    if end < 2:
        if len(buf) > 65536:
            buf.clear()
        return None
    jpeg = bytes(buf[: end + 2])
    del buf[: end + 2]
    return jpeg


async def _append_camera_binary(device_id: str, chunk: bytes) -> Optional[bytes]:
    if not device_id or not chunk:
        return None
    buf = _cam_bin_buf.setdefault(device_id, bytearray())
    buf.extend(chunk)
    return _try_extract_jpeg(device_id)


async def _supersede_camera_uplink(device_id: str, websocket) -> None:
    prev = _active_camera_uplink.get(device_id)
    if prev is None or prev is websocket:
        _active_camera_uplink[device_id] = websocket
        return
    logger.info(
        "[/camera_uplink] 关闭旧连接 device_id=%s (新 peer 接入)",
        device_id,
    )
    try:
        await prev.close(code=1000, reason="superseded by new camera_uplink")
    except Exception:
        logger.warning(
            "[/camera_uplink] 旧连接 close 异常 device_id=%s",
            device_id,
            exc_info=True,
        )
    _active_camera_uplink[device_id] = websocket


async def _ingest_camera_frame(
    *,
    payload: bytes,
    enc: str,
    device_id: Optional[str],
    camera_pipe: Optional[AsrChatCameraPipeline],
    camera_image_broker: CameraImageBroker,
    dp_broker: DevicePipelineBroker,
    asr_chat_hub: AsrChatHub,
    send_face_info_to_asr_chat: bool,
    camera_task_holder: list,
    api_key_id: Optional[str],
) -> None:
    if device_id:
        from deskbot_server.device_camera_frame_store import (
            update_device_camera_frame,
        )

        update_device_camera_frame(
            device_id,
            payload,
            source="camera_uplink",
        )
    if camera_pipe is not None:
        if api_key_id:
            record_turn_usage(
                api_key_id, device_id=device_id, face_bytes=len(payload)
            )
        await _schedule_camera_jpeg(
            camera_pipe,
            payload,
            image_broker=camera_image_broker,
            dp_broker=dp_broker,
            asr_chat_hub=asr_chat_hub,
            send_face_info_to_asr_chat=send_face_info_to_asr_chat,
            camera_task_holder=camera_task_holder,
            device_id=device_id,
        )
    logger.info(
        "[/camera_uplink] camera_frame ok device_id=%s bytes=%d enc=%s",
        device_id,
        len(payload),
        enc,
    )


async def handle_camera_uplink(
    websocket,
    device_id: Optional[str],
    registry: DeviceRegistry,
    dp_broker: DevicePipelineBroker,
    asr_chat_hub: AsrChatHub,
    camera_image_broker: CameraImageBroker,
    camera_face_runtime: CameraFaceRuntime,
    *,
    send_face_info_to_asr_chat: bool = False,
    api_key_id: Optional[str] = None,
) -> None:
    peer = _peer_str(websocket)
    camera_task_holder: list[asyncio.Task] = []
    camera_pipe: Optional[AsrChatCameraPipeline] = None
    if device_id and camera_face_runtime is not None:
        device_runtime = build_camera_face_runtime(load_config(), device_id=device_id)
        camera_pipe = AsrChatCameraPipeline(runtime=device_runtime, device_id=device_id)

    if device_id:
        await _supersede_camera_uplink(device_id, websocket)
        await registry.connect(device_id, "camera_uplink", websocket)
        logger.info(
            "[/camera_uplink] 接入 device_id=%s peer=%s (独立相机 WS，不经 asr_chat_hub)",
            device_id,
            peer,
        )
    else:
        logger.warning(
            "[/camera_uplink] 缺失 device_id peer=%s —— 帧不入库",
            peer,
        )

    try:
        await _safe_send(
            websocket,
            _json_msg(
                {
                    "type": "ready",
                    "channel": "camera_uplink",
                    "device_id": device_id,
                    "accept_binary_jpeg": True,
                }
            ),
        )

        async for message in websocket:
            if isinstance(message, (bytes, bytearray)):
                chunk = bytes(message)
                if len(chunk) < 1:
                    continue
                if not device_id:
                    continue
                payload = await _append_camera_binary(device_id, chunk)
                if payload is None:
                    continue
                if len(payload) < 64:
                    logger.warning(
                        "[/camera_uplink] device_id=%s JPEG 过短 bytes=%d",
                        device_id,
                        len(payload),
                    )
                    continue
                enc = "binary"
            else:
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    logger.warning("[/camera_uplink] device_id=%s JSON 解析失败", device_id)
                    continue

                msg_type = data.get("type")
                if msg_type == "ping":
                    await _safe_send(websocket, _json_msg({"type": "pong"}))
                    continue

                if msg_type != "camera_frame":
                    continue

                raw_b64 = data.get("data")
                if not raw_b64:
                    logger.warning(
                        "[/camera_uplink] camera_frame 缺少 data device_id=%s",
                        device_id,
                    )
                    continue
                try:
                    payload = base64.b64decode(raw_b64)
                except Exception:
                    logger.warning(
                        "[/camera_uplink] camera_frame base64 解码失败 device_id=%s",
                        device_id,
                    )
                    continue
                enc = "base64"

            if device_id:
                asyncio.create_task(
                    _ingest_camera_frame(
                        payload=payload,
                        enc=enc,
                        device_id=device_id,
                        camera_pipe=camera_pipe,
                        camera_image_broker=camera_image_broker,
                        dp_broker=dp_broker,
                        asr_chat_hub=asr_chat_hub,
                        send_face_info_to_asr_chat=send_face_info_to_asr_chat,
                        camera_task_holder=camera_task_holder,
                        api_key_id=api_key_id,
                    )
                )

    except ConnectionClosed:
        logger.info("[/camera_uplink] 连接关闭 device_id=%s peer=%s", device_id, peer)
    finally:
        if device_id and _active_camera_uplink.get(device_id) is websocket:
            _active_camera_uplink.pop(device_id, None)
            _cam_bin_buf.pop(device_id, None)
        if device_id:
            await registry.disconnect(websocket)
        prev = camera_task_holder[0] if camera_task_holder else None
        if prev is not None and not prev.done():
            prev.cancel()
