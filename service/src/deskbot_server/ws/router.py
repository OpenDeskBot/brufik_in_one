from __future__ import annotations

import logging

from deskbot_server.constants import CAMERA_VIEW_PATH, DEVICE_PIPELINE_PATH
from deskbot_server.pipeline.audio import AudioConfig
from deskbot_server.application.chat_service import ChatService
from deskbot_server.util import (
    _extract_device_id,
    _json_msg,
    _parse_query,
    _peer_str,
    _split_path,
    _ws_request_path,
)
from deskbot_server.vision.undistort import CameraFaceRuntime
from deskbot_server.ws.api_key_gate import ws_require_api_key, ws_require_debug_subscriber_auth
from deskbot_server.ws.asr_chat import handle_asr_chat
from deskbot_server.ws.asr_chat_hub import AsrChatHub
from deskbot_server.application.camera_broker import CameraImageBroker
from deskbot_server.ws.camera import handle_camera_view
from deskbot_server.ws.device_pipeline import DevicePipelineBroker, handle_device_pipeline
from deskbot_server.ws.registry import DeviceRegistry
from deskbot_server.ws.ws_send import _safe_send

logger = logging.getLogger("deskbot-server")

_LEGACY_CAMERA_PATH = "/camera"


async def handle_client(
    websocket,
    pipeline: ChatService,
    audio_cfg: AudioConfig,
    ws_path: str,
    device_pipeline_broker: DevicePipelineBroker,
    registry: DeviceRegistry,
    asr_chat_hub: AsrChatHub,
    camera_image_broker: CameraImageBroker,
    camera_face_runtime: CameraFaceRuntime,
):
    raw_path = _ws_request_path(websocket)
    path_only, query = _split_path(raw_path)
    peer = _peer_str(websocket)
    logger.info("[WS] 收到连接 peer=%s path=%s", peer, raw_path)

    if path_only == _LEGACY_CAMERA_PATH:
        logger.warning(
            "[WS] 拒绝已移除的 /camera peer=%s —— 请改用 /asr_chat?device_id= 上传 camera_frame",
            peer,
        )
        await _safe_send(
            websocket,
            _json_msg(
                {
                    "type": "error",
                    "message": "/camera 已移除；请使用 /asr_chat 的 camera_frame + next_bin_len",
                }
            ),
        )
        await websocket.close(code=1008, reason="/camera removed; use /asr_chat camera_frame")
        return

    qargs = _parse_query(query)

    if path_only == CAMERA_VIEW_PATH:
        device_id = _extract_device_id(qargs)
        if not await ws_require_debug_subscriber_auth(
            websocket,
            qargs,
            device_id=device_id,
            require_device=True,
        ):
            return
        await handle_camera_view(websocket, camera_image_broker)
        return

    if path_only == DEVICE_PIPELINE_PATH:
        role = (qargs.get("role") or "").lower()
        is_subscriber = role in ("subscriber", "sub", "viewer", "consumer")
        if is_subscriber:
            device_id = _extract_device_id(qargs)
            if not await ws_require_debug_subscriber_auth(
                websocket,
                qargs,
                device_id=device_id,
                require_device=True,
            ):
                return
        else:
            api_auth = await ws_require_api_key(websocket, qargs)
            if api_auth is None:
                return
        await handle_device_pipeline(websocket, device_pipeline_broker, registry)
        return

    if path_only and path_only != ws_path:
        logger.warning(
            "[WS] 拒绝非法路径 peer=%s path=%s "
            "(期望 asr_chat=%s, camera_view=%s, device_pipeline=%s)",
            peer,
            raw_path,
            ws_path,
            CAMERA_VIEW_PATH,
            DEVICE_PIPELINE_PATH,
        )
        await websocket.close(code=1008, reason=f"unsupported path: {raw_path}")
        return

    device_id = _extract_device_id(qargs)
    api_auth = await ws_require_api_key(websocket, qargs)
    if api_auth is None:
        return

    await handle_asr_chat(
        websocket,
        pipeline,
        audio_cfg,
        device_id,
        registry,
        device_pipeline_broker,
        asr_chat_hub,
        camera_image_broker,
        camera_face_runtime,
        send_face_info_to_asr_chat=pipeline.settings.server.send_face_info_to_asr_chat,
        api_key_id=api_auth.api_key_id,
    )
