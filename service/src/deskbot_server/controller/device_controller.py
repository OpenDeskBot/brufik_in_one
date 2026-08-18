"""设备侧 Controller：``/asr_chat`` WebSocket。"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket

from deskbot_server.controller.auth import require_device_ws
from deskbot_server.utils.ws_utils import WsUtils
from deskbot_server.service.device_ws_service import DeviceWsService

router = APIRouter(tags=["device"])

ASR_CHAT_PATH = "/asr_chat"


@router.websocket("/asr_chat")
@require_device_ws
async def asr_chat(websocket: WebSocket) -> None:
    st = websocket.state
    ws = st.ws
    device_id = st.device_id
    if device_id:
        await WsUtils.keep_only_one_link(device_id, ASR_CHAT_PATH, ws)
    try:
        await DeviceWsService().handle_asr_chat(ws, device_id)
    finally:
        if device_id:
            WsUtils.release_link(device_id, ASR_CHAT_PATH, ws)
