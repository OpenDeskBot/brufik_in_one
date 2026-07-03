"""WebSocket 对话轮次：下行适配 + application/chat_flow。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from deskbot_server.application.chat_flow import publish_chat_turn, run_chat_turn
from deskbot_server.core.types import ChatTurnResult
from deskbot_server.infrastructure.ws.downlink_adapter import WsDownlinkAdapter, WsPipelineEventsAdapter

if TYPE_CHECKING:
    from deskbot_server.application.chat_service import ChatService
    from deskbot_server.ws.device_pipeline import DevicePipelineBroker
    from deskbot_server.ws.registry import DeviceRegistry


async def run_ws_chat_turn(
    websocket,
    pipeline: ChatService,
    user_text: str,
    *,
    request_id: Optional[str] = None,
    dp_broker: Optional[DevicePipelineBroker] = None,
    registry: Optional[DeviceRegistry] = None,
    device_id: Optional[str] = None,
    t_asr_start: Optional[float] = None,
    t_asr_text: Optional[float] = None,
    asr_chat_hub: Optional[Any] = None,
    on_llm_error: Optional[Any] = None,
) -> dict:
    downlink = WsDownlinkAdapter(
        websocket,
        settings=pipeline.settings,
        device_id=device_id,
        dp_broker=dp_broker,
    )
    turn = await run_chat_turn(
        downlink,
        pipeline,
        user_text,
        request_id=request_id,
        device_id=device_id,
        registry=registry,
        t_asr_start=t_asr_start,
        t_asr_text=t_asr_text,
        pipeline_broker=dp_broker,
        asr_chat_hub=asr_chat_hub,
        on_llm_error=on_llm_error,
    )
    return turn.as_dict()


async def publish_ws_chat_turn(
    broker: DevicePipelineBroker,
    registry: DeviceRegistry,
    device_id: Optional[str],
    *,
    source: str,
    asr_text: Optional[str],
    t_asr_start: Optional[float],
    t_asr_text: Optional[float],
    flow: dict,
    request_id: Optional[str] = None,
) -> None:
    if not device_id or broker is None:
        return
    events = WsPipelineEventsAdapter(broker, registry)
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
