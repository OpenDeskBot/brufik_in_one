from __future__ import annotations

import logging

from deskbot_server.core.ports.tts import TtsPort
from deskbot_server.core.settings import AppSettings
from deskbot_server.infrastructure.tts.doubao_phoneme import DoubaoPhonemeTtsAdapter

logger = logging.getLogger("deskbot-server")


def build_tts_adapter(settings: AppSettings) -> TtsPort:
    """按 ``tts.provider`` / ``TTS_PROVIDER`` 选择 TTS 后端（当前仅支持 doubao）。"""
    provider = (settings.tts.provider or "doubao").strip().lower()
    if provider != "doubao":
        logger.warning("[TTS] 未知 provider=%r，使用 doubao", provider)
    logger.info("[TTS] provider=doubao（时间戳/拼音均分音素口型）")
    return DoubaoPhonemeTtsAdapter(settings)
