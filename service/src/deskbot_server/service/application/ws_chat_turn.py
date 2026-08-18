"""WebSocket 对话轮次：下行适配 + application/chat_flow。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from deskbot_server.infrastructure.ws.downlink_adapter import WsDownlinkAdapter, WsPipelineEventsAdapter
from deskbot_server.model.chat import ChatTurnResult
from deskbot_server.service.application.chat_flow import publish_chat_turn, run_chat_turn

if TYPE_CHECKING:
    from deskbot_server.service.application.chat_service import ChatService
    from deskbot_server.service.bus_service import BusService
    from deskbot_server.service.device_ws_service import DeviceWsService


async def run_ws_chat_turn(
    websocket,
    pipeline: ChatService,
    user_text: str,
    *,
    request_id: str | None = None,
    device_ws: DeviceWsService | None = None,
    device_id: str | None = None,
    t_asr_start: float | None = None,
    t_asr_text: float | None = None,
    on_llm_error: Any | None = None,
    bus_service: BusService | None = None,
) -> dict:
    downlink = WsDownlinkAdapter(websocket, settings=pipeline.settings, device_id=device_id, bus_service=bus_service)
    turn = await run_chat_turn(
        downlink,
        pipeline,
        user_text,
        request_id=request_id,
        device_id=device_id,
        device_ws=device_ws,
        t_asr_start=t_asr_start,
        t_asr_text=t_asr_text,
        on_llm_error=on_llm_error,
        bus_service=bus_service,
    )
    return turn.as_dict()


async def publish_ws_chat_turn(
    bus: Any,
    device_ws: DeviceWsService,
    device_id: str | None,
    *,
    source: str,
    asr_text: str | None,
    t_asr_start: float | None,
    t_asr_text: float | None,
    flow: dict,
    request_id: str | None = None,
) -> None:
    if not device_id or bus is None:
        return
    events = WsPipelineEventsAdapter(bus, device_ws)
    turn = ChatTurnResult(
        llm_text=flow.get("llm_text"),
        llm_raw=flow.get("llm_raw"),
        moves=list(flow.get("moves") or []),
        anims=list(flow.get("anims") or []),
        tools=list(flow.get("tools") or []),
        tool_results=list(flow.get("tool_results") or []),
        servo=list(flow.get("servo") or []),
        need_reply=bool(flow.get("need_reply", True)),
        json_ok=bool(flow.get("json_ok")),
        t_llm_end=flow.get("t_llm_end"),
        t_tts_synth_end=flow.get("t_tts_synth_end"),
        t_tts_end=flow.get("t_tts_end"),
        status=flow.get("status") or "ok",
        error=flow.get("error"),
        voice_auto_reply_off=bool(flow.get("voice_auto_reply_off")),
        scenes=list(flow.get("scenes") or []),
    )
    await publish_chat_turn(
        events,
        device_id,
        source=source,
        asr_text=asr_text,
        t_asr_start=t_asr_start,
        t_asr_text=t_asr_text,
        turn=turn,
        request_id=request_id,
    )
