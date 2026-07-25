"""设备侧 Controller：``/asr_chat`` / ``/camera_uplink`` WebSocket。"""

from __future__ import annotations

import base64
import json
import logging
from typing import Optional

from fastapi import APIRouter, WebSocket
from websockets.exceptions import ConnectionClosed

from deskbot_server.constants import CAMERA_UPLINK_PATH
from deskbot_server.controller.auth import require_device_ws_auth
from deskbot_server.controller.runtime import get_runtime
from deskbot_server.service.application.asr_chat_uplink import (
    coerce_next_bin_len,
    pack_ws_downlink_frame,
    parse_packed_frame,
)
from deskbot_server.service.camera_face_service import CameraFaceService
from deskbot_server.utils.async_helpers import spawn
from deskbot_server.utils.util import _json_msg
from deskbot_server.utils.ws_utils import WsUtils
from deskbot_server.ws.api_key_gate import record_turn_usage
from deskbot_server.ws.asr_chat import handle_asr_chat

logger = logging.getLogger("deskbot-server")

router = APIRouter(tags=["device"])

ASR_CHAT_PATH = "/asr_chat"


@router.websocket("/asr_chat")
@require_device_ws_auth
async def asr_chat(websocket: WebSocket) -> None:
    rt = get_runtime()
    st = websocket.state
    ws = st.ws
    device_id = st.device_id
    if device_id:
        await WsUtils.keep_only_one_link(device_id, ASR_CHAT_PATH, ws)
    try:
        await handle_asr_chat(
            ws,
            rt.chat,
            rt.audio_cfg,
            device_id,
            rt.registry,
            rt.device_pipeline_broker,
            rt.asr_chat_hub,
            api_key_id=st.api_auth.api_key_id,
        )
    finally:
        if device_id:
            WsUtils.release_link(device_id, ASR_CHAT_PATH, ws)


@router.websocket("/camera_uplink")
@require_device_ws_auth
async def camera_uplink(websocket: WebSocket) -> None:
    """管理连接并读取上行 JPEG。

    协议与 ``/asr_chat`` 的 camera_frame 一致：打包 BIN
    ``u32be(json_len)+camera_frame JSON+JPEG``。
    仍兼容旧 TEXT JSON + 下一条独立 BIN，以及 JSON ``data`` base64（调试用）。
    """
    rt = get_runtime()
    st = websocket.state
    ws = st.ws
    device_id = st.device_id
    api_key_id = st.api_auth.api_key_id
    registry = rt.registry
    face_svc = CameraFaceService()

    peer = WsUtils.peer_str(ws)
    pending_len: Optional[int] = None

    if device_id:
        await WsUtils.keep_only_one_link(device_id, CAMERA_UPLINK_PATH, ws)
        await registry.connect(device_id, "camera_uplink", ws)
        logger.info("[/camera_uplink] 接入 device_id=%s peer=%s (只收帧，不回写设备)", device_id, peer)
    else:
        logger.warning("[/camera_uplink] 缺失 device_id peer=%s —— 帧不处理", peer)

    try:
        await WsUtils.safe_send(
            ws,
            pack_ws_downlink_frame(
                _json_msg(
                    {
                        "type": "ready",
                        "channel": "camera_uplink",
                        "device_id": device_id,
                        "expects": "packed BIN: camera_frame JSON + JPEG",
                    }
                )
            ),
        )

        async for message in ws:
            attached_media: Optional[bytes] = None
            # --- 等待中的 binary（上一帧 JSON 已声明 next_bin_len）---
            if pending_len is not None:
                if not isinstance(message, (bytes, bytearray)):
                    logger.warning(
                        "[/camera_uplink] device_id=%s 预期 %d 字节 binary，收到非 binary，丢弃 pending",
                        device_id,
                        pending_len,
                    )
                    pending_len = None
                    continue
                payload = bytes(message)
                expected = pending_len
                pending_len = None
                if len(payload) != expected:
                    logger.warning(
                        "[/camera_uplink] device_id=%s binary 长度不符 expected=%d got=%d",
                        device_id,
                        expected,
                        len(payload),
                    )
                    continue
                if len(payload) < 64:
                    logger.warning("[/camera_uplink] device_id=%s JPEG 过短 bytes=%d", device_id, len(payload))
                    continue
                if not device_id:
                    continue
                if api_key_id:
                    record_turn_usage(api_key_id, device_id=device_id, face_bytes=len(payload))
                spawn(
                    face_svc.process(
                        device_id, payload, frame_source="camera_uplink", log_channel="/camera_uplink"
                    ),
                    name=f"camera_uplink_process:{device_id}",
                )
                continue

            if isinstance(message, (bytes, bytearray)):
                payload = bytes(message)
                frame = parse_packed_frame(payload)
                if frame is None:
                    logger.warning(
                        "[/camera_uplink] device_id=%s 收到未声明的 binary bytes=%d（需打包帧或先发 camera_frame JSON）",
                        device_id,
                        len(payload),
                    )
                    continue
                data = frame.doc
                attached_media = frame.bin
            else:
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    logger.warning("[/camera_uplink] device_id=%s JSON 解析失败", device_id)
                    continue

            if data.get("type") != "camera_frame":
                continue

            # 调试兼容：整段 base64 在 JSON 内
            raw_b64 = data.get("data")
            if raw_b64:
                try:
                    payload = base64.b64decode(raw_b64)
                except Exception:
                    logger.warning("[/camera_uplink] camera_frame base64 解码失败 device_id=%s", device_id)
                    continue
                if not device_id:
                    continue
                if api_key_id:
                    record_turn_usage(api_key_id, device_id=device_id, face_bytes=len(payload))
                spawn(
                    face_svc.process(
                        device_id, payload, frame_source="camera_uplink", log_channel="/camera_uplink"
                    ),
                    name=f"camera_uplink_process:{device_id}",
                )
                continue

            nbl = coerce_next_bin_len(data)
            if nbl > 0:
                if attached_media is not None:
                    if len(attached_media) != nbl:
                        logger.warning(
                            "[/camera_uplink] device_id=%s packed binary 长度不符 expected=%d got=%d",
                            device_id,
                            nbl,
                            len(attached_media),
                        )
                        continue
                    if len(attached_media) < 64:
                        logger.warning(
                            "[/camera_uplink] device_id=%s JPEG 过短 bytes=%d", device_id, len(attached_media)
                        )
                        continue
                    if not device_id:
                        continue
                    if api_key_id:
                        record_turn_usage(api_key_id, device_id=device_id, face_bytes=len(attached_media))
                    spawn(
                        face_svc.process(
                            device_id,
                            attached_media,
                            frame_source="camera_uplink",
                            log_channel="/camera_uplink",
                        ),
                        name=f"camera_uplink_process:{device_id}",
                    )
                    continue
                if pending_len is not None:
                    logger.warning(
                        "[/camera_uplink] camera_frame 覆盖未完成的 pending device_id=%s old_len=%d new_len=%d",
                        device_id,
                        pending_len,
                        nbl,
                    )
                pending_len = nbl
                continue

            logger.warning("[/camera_uplink] camera_frame 缺少 next_bin_len device_id=%s", device_id)

    except ConnectionClosed:
        logger.info("[/camera_uplink] 连接关闭 device_id=%s peer=%s", device_id, peer)
    finally:
        if device_id:
            WsUtils.release_link(device_id, CAMERA_UPLINK_PATH, ws)
        if device_id:
            await registry.disconnect(ws)
