"""设备流水线事件服务：ASR/LLM/TTS 调试事件滚动窗口 + 订阅广播。"""

from __future__ import annotations

from typing import Optional

from deskbot_server.utils.singleton import SingletonMeta
from deskbot_server.ws.device_pipeline import DevicePipelineBroker


class PipelineService(metaclass=SingletonMeta):
    """对 ``DevicePipelineBroker`` 的单例门面（主要供 Web 调试订阅）。"""

    def __init__(self) -> None:
        self._broker: DevicePipelineBroker | None = None

    def bind(self, broker: DevicePipelineBroker) -> None:
        self._broker = broker

    @property
    def broker(self) -> DevicePipelineBroker:
        if self._broker is None:
            raise RuntimeError("PipelineService 尚未 bind")
        return self._broker

    async def publish(self, event: dict) -> dict:
        return await self.broker.publish(event)

    async def has_subscribers_for_device(self, device_id: Optional[str] = None) -> bool:
        return await self.broker.has_subscribers_for_device(device_id)

    async def broadcast_to_device(self, device_id: str, payload: dict) -> None:
        await self.broker.broadcast_to_device(device_id, payload)

    def snapshot_events(self, device_id: Optional[str] = None, limit: int = 100) -> list:
        return self.broker.snapshot_events(device_id, limit)
