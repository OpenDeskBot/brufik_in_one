"""设备侧 Controller：``/asr_chat`` WebSocket。"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket

from deskbot_server.controller.auth import require_device_ws
from deskbot_server.controller.runtime import get_runtime
from deskbot_server.utils.ws_utils import WsUtils
from deskbot_server.ws.asr_chat import handle_asr_chat

router = APIRouter(tags=["device"])

ASR_CHAT_PATH = "/asr_chat"


@router.websocket("/asr_chat")
@require_device_ws
async def asr_chat(websocket: WebSocket) -> None:
    rt = get_runtime()
    st = websocket.state
    ws = st.ws
    device_id = st.device_id
    if device_id:
        await WsUtils.keep_only_one_link(device_id, ASR_CHAT_PATH, ws)
    try:
        await handle_asr_chat(
            ws, rt.chat, rt.audio_cfg, device_id, rt.registry, rt.device_pipeline_broker, rt.asr_chat_hub
        )
    finally:
        if device_id:
            WsUtils.release_link(device_id, ASR_CHAT_PATH, ws)
