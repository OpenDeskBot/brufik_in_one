from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Optional

from deskbot_server.core.settings import AppSettings
from deskbot_server.ws.device_pipeline import DevicePipelineBroker
from deskbot_server.ws.stages import _emit_stage
from deskbot_server.ws.ws_send import (
    _maybe_pb_serial_chain_guard,
    _send_pb_wire_to_asr_device,
)


class WsDownlinkAdapter:
    """WebSocket 下行适配器：实现 DownlinkPort。"""

    def __init__(
        self,
        websocket,
        *,
        settings: AppSettings,
        device_id: Optional[str],
        dp_broker: Optional[DevicePipelineBroker],
    ) -> None:
        self._ws = websocket
        self._settings = settings
        self._device_id = device_id
        self._broker = dp_broker

    async def emit_stage(
        self,
        stage: str,
        *,
        request_id: Optional[str],
        client_fields: Optional[dict[str, Any]] = None,
        event_fields: Optional[dict[str, Any]] = None,
        send_client: bool = True,
    ) -> None:
        await _emit_stage(
            self._ws,
            self._broker,
            self._device_id,
            request_id,
            stage,
            client_fields=client_fields,
            event_fields=event_fields,
            send_client=send_client,
        )

    async def send_pb_wire(
        self, wire_text: str, binaries: Optional[list[bytes]] = None, pcm: Optional[bytes] = None
    ) -> bool:
        return await _send_pb_wire_to_asr_device(
            self._ws, wire_text, binaries=binaries, pcm=pcm
        )

    @asynccontextmanager
    async def pb_serial_chain(self):
        async with _maybe_pb_serial_chain_guard(self._ws):
            yield


class WsPipelineEventsAdapter:
    """DevicePipelineBroker + DeviceRegistry 的 PipelineEventsPort 实现。"""

    def __init__(self, broker: DevicePipelineBroker, registry) -> None:
        self._broker = broker
        self._registry = registry

    async def publish_turn(self, event: dict[str, Any]) -> None:
        await self._broker.publish(event)

    async def touch_device(self, device_id: str, status: str) -> None:
        await self._registry.touch(device_id, status)
