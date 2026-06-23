from __future__ import annotations

import logging

from deskbot_server.core.ports.tts import TtsPort
from deskbot_server.core.settings import AppSettings
from deskbot_server.infrastructure.tts.doubao_phoneme import DoubaoPhonemeTtsAdapter
from deskbot_server.infrastructure.tts.paddle_phoneme import PaddlePhonemeTtsAdapter

logger = logging.getLogger("deskbot-server")


def build_tts_adapter(settings: AppSettings) -> TtsPort:
    """按 ``tts.provider`` / ``TTS_PROVIDER`` 选择 TTS 后端。"""
    provider = (settings.tts.provider or "paddle").strip().lower()
    if provider == "doubao":
        logger.info("[TTS] provider=doubao（时间戳/拼音均分音素口型）")
        return DoubaoPhonemeTtsAdapter(settings)
    if provider not in ("paddle", ""):
        logger.warning("[TTS] 未知 provider=%r，回退 paddle", provider)
    return PaddlePhonemeTtsAdapter(settings)
