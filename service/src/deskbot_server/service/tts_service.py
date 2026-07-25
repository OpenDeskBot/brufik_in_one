"""TTS 合成服务。"""

from __future__ import annotations

from deskbot_server.core.ports.tts import TtsPort
from deskbot_server.utils.singleton import SingletonMeta


class TtsService(metaclass=SingletonMeta):
    def __init__(self) -> None:
        self._tts: TtsPort | None = None

    def bind(self, tts: TtsPort) -> None:
        self._tts = tts

    @property
    def tts(self) -> TtsPort:
        if self._tts is None:
            raise RuntimeError("TtsService 尚未 bind，请先在 bootstrap 中装配")
        return self._tts

    @staticmethod
    def _seg_to_dict(s) -> dict:
        if isinstance(s, dict):
            return {
                "phoneme": s.get("phoneme"),
                "ms": s.get("ms"),
                "pcm": s.get("pcm"),
                "phoneme_id": s.get("phoneme_id"),
            }
        return {"phoneme": s.phoneme, "ms": s.ms, "pcm": s.pcm, "phoneme_id": s.phoneme_id}

    async def synthesize_phoneme_segments(self, text: str) -> tuple[int, list[dict]]:
        sr, segs = await self.tts.synthesize_phoneme_segments(text)
        return sr, [self._seg_to_dict(s) for s in segs]
