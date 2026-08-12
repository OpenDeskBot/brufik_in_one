"""语音识别服务。"""

from __future__ import annotations

from deskbot_server.ports.asr import AsrPort
from deskbot_server.utils.singleton import SingletonMeta


class AsrService(metaclass=SingletonMeta):
    def __init__(self) -> None:
        self._asr: AsrPort | None = None

    def bind(self, asr: AsrPort) -> None:
        self._asr = asr

    @property
    def asr(self) -> AsrPort:
        if self._asr is None:
            raise RuntimeError("AsrService 尚未 bind，请先在 bootstrap 中装配")
        return self._asr

    async def transcribe(self, pcm_bytes: bytes, sample_rate: int) -> str:
        return await self.asr.transcribe(pcm_bytes, sample_rate)

    def is_valid_text(self, text: str) -> bool:
        return self.asr.is_valid_text(text)
