from __future__ import annotations

from typing import Any

from deskbot_server.model.settings import AppSettings
from deskbot_server.ws.stages import _emit_stage


class WsDownlinkAdapter:
    """WebSocket 下行适配器：实现 DownlinkPort。"""

    def __init__(
        self,
        websocket,
        *,
        settings: AppSettings,
        device_id: str | None,
        bus_service: Any | None = None,
    ) -> None:
        self._ws = websocket
        self._settings = settings
        self._device_id = device_id
        self._bus_service = bus_service

    async def emit_stage(
        self,
        stage: str,
        *,
        request_id: str | None,
        client_fields: dict[str, Any] | None = None,
        event_fields: dict[str, Any] | None = None,
        send_client: bool = True,
    ) -> None:
        await _emit_stage(
            self._ws,
            self._device_id,
            request_id,
            stage,
            client_fields=client_fields,
            event_fields=event_fields,
            send_client=send_client,
            bus_service=self._bus_service,
        )


class WsPipelineEventsAdapter:
    """BusService + DeviceWsService 的 PipelineEventsPort 实现。"""

    def __init__(self, bus: Any, device_ws) -> None:
        self._bus = bus
        self._device_ws = device_ws

    async def publish_turn(self, event: dict[str, Any]) -> None:
        device_id = str(event.get("device_id") or "unknown")
        await self._bus.pub(device_id, event)

    async def touch_device(self, device_id: str, status: str) -> None:
        await self._device_ws.touch(device_id, status)
