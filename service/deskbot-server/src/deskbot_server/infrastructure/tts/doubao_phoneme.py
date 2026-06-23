from __future__ import annotations

import logging

from deskbot_server.core.ports.tts import PhonemeSegment
from deskbot_server.core.settings import AppSettings
from deskbot_server.tts.doubao import load_doubao_tts_config, synthesize_doubao_tts
from deskbot_server.tts.doubao_phoneme_align import build_phoneme_segments

logger = logging.getLogger("deskbot-server")


class DoubaoPhonemeTtsAdapter:
    """豆包 TTS 适配器：时间戳 / 拼音均分 → 音素分片（口型）。"""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    async def synthesize_phoneme_segments(self, text: str) -> tuple[int, list[PhonemeSegment]]:
        clean = (text or "").strip()
        if not clean:
            sr = int(self._settings.tts.sample_rate or 24000)
            return sr, []

        cfg = load_doubao_tts_config()
        result = await synthesize_doubao_tts(clean, cfg)
        pcm = bytes(result.pcm or b"")
        sr = int(result.sample_rate or cfg.sample_rate or 24000)
        if not pcm:
            raise RuntimeError(f"豆包 TTS 无 PCM: {clean!r}")

        segs = build_phoneme_segments(
            text=clean,
            pcm=pcm,
            sample_rate=sr,
            sentence_end=result.sentence_end,
            subtitles=result.subtitles,
        )
        logger.info(
            "[TTS/doubao] 音素分片 n=%d pcm_bytes=%d elapsed_ms=%d text=%r",
            len(segs),
            len(pcm),
            result.elapsed_ms,
            clean[:80] + ("…" if len(clean) > 80 else ""),
        )
        return sr, segs
