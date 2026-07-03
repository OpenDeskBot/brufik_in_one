from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any, Optional, Protocol


class PipelineEventsPort(Protocol):
    async def publish_turn(self, event: dict[str, Any]) -> None: ...

    async def touch_device(self, device_id: str, status: str) -> None: ...


class DownlinkPort(Protocol):
    """应用层与设备下行解耦：由 infrastructure/ws 适配 WebSocket 实现。"""

    async def emit_stage(
        self,
        stage: str,
        *,
        request_id: Optional[str],
        client_fields: Optional[dict[str, Any]] = None,
        event_fields: Optional[dict[str, Any]] = None,
        send_client: bool = True,
    ) -> None: ...

    async def send_pb_wire(
        self, wire_text: str, binaries: Optional[list[bytes]] = None, pcm: Optional[bytes] = None
    ) -> bool: ...

    def pb_serial_chain(self) -> AbstractAsyncContextManager[None]: ...
