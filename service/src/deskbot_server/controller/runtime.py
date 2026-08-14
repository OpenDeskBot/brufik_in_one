"""进程内共享运行时（lifespan 装配）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deskbot_server.model.settings import AppSettings
    from deskbot_server.service.application.chat_service import ChatService
    from deskbot_server.service.pipeline.audio import AudioConfig
    from deskbot_server.ws.asr_chat_hub import AsrChatHub
    from deskbot_server.ws.device_pipeline import DevicePipelineBroker
    from deskbot_server.ws.registry import DeviceRegistry


@dataclass
class AppRuntime:
    settings: AppSettings
    chat: ChatService
    audio_cfg: AudioConfig
    ws_path: str
    device_pipeline_broker: DevicePipelineBroker
    registry: DeviceRegistry
    asr_chat_hub: AsrChatHub
    scheduler: object | None = None


_RUNTIME: AppRuntime | None = None


def set_runtime(runtime: AppRuntime) -> None:
    global _RUNTIME
    _RUNTIME = runtime


def get_runtime() -> AppRuntime:
    if _RUNTIME is None:
        raise RuntimeError("AppRuntime 未初始化")
    return _RUNTIME
