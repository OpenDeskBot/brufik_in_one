from __future__ import annotations

from typing import Any, Protocol


class PipelineEventsPort(Protocol):
    async def publish_turn(self, event: dict[str, Any]) -> None: ...

    async def touch_device(self, device_id: str, status: str) -> None: ...


class DownlinkPort(Protocol):
    """应用层与设备下行解耦：由 infrastructure/ws 适配 WebSocket 实现。"""

    async def emit_stage(
        self,
        stage: str,
        *,
        request_id: str | None,
        client_fields: dict[str, Any] | None = None,
        event_fields: dict[str, Any] | None = None,
        send_client: bool = True,
    ) -> None: ...
