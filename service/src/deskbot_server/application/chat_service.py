from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Optional

from deskbot_server.core.ports.asr import AsrPort
from deskbot_server.core.ports.llm import LlmPort
from deskbot_server.core.ports.tts import TtsPort
from deskbot_server.core.settings import AppSettings


class ChatService:
    """应用服务：组合 ASR / LLM / TTS 端口，不含 WebSocket 细节。"""

    def __init__(
        self,
        settings: AppSettings,
        *,
        asr: AsrPort,
        llm: LlmPort,
        tts: TtsPort,
    ) -> None:
        self.settings = settings
        self._asr = asr
        self._llm = llm
        self._tts = tts

    # --- 兼容旧 BotPipeline 属性 ---
    @property
    def config(self) -> dict:
        return self.settings.raw

    @property
    def tts_cfg(self) -> dict:
        return self.settings.tts_cfg

    @property
    def asr_chat_device_pb_only(self) -> bool:
        return self.settings.server.asr_chat_device_pb_only

    @property
    def asr_chat_minimal_device_downlink(self) -> bool:
        return self.settings.server.asr_chat_minimal_device_downlink

    async def asr(self, pcm_bytes: bytes, sample_rate: int) -> str:
        return await self._asr.transcribe(pcm_bytes, sample_rate)

    def is_valid_asr_text(self, text: str) -> bool:
        return self._asr.is_valid_text(text)

    async def llm(
        self,
        text: str,
        *,
        device_context: str | None = None,
        device_id: str | None = None,
        history_messages: list[dict[str, str]] | None = None,
        extra_messages: list[dict[str, str]] | None = None,
        on_tts_ready: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> str:
        return await self._llm.complete(
            text,
            device_context=device_context,
            device_id=device_id,
            history_messages=history_messages,
            extra_messages=extra_messages,
            on_tts_ready=on_tts_ready,
        )

    async def tts_phoneme_segments(self, text: str) -> tuple[int, list[dict]]:
        sr, segs = await self._tts.synthesize_phoneme_segments(text)
        return sr, [
            {
                "phoneme": s.phoneme,
                "ms": s.ms,
                "pcm": s.pcm,
                "phoneme_id": s.phoneme_id,
            }
            for s in segs
        ]

