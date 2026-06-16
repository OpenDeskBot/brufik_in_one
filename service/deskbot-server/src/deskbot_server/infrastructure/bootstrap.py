"""Composition Root 辅助：装配 ChatService 与基础设施适配器。"""

from __future__ import annotations

from deskbot_server.application.chat_service import ChatService
from deskbot_server.core.settings import AppSettings
from deskbot_server.infrastructure.asr.funasr import FunAsrAdapter
from deskbot_server.infrastructure.llm.openai_compat import OpenAiLlmAdapter
from deskbot_server.infrastructure.tts.paddle_phoneme import PaddlePhonemeTtsAdapter


def build_chat_service(config: dict) -> ChatService:
    settings = AppSettings.from_config(config)
    return ChatService(
        settings,
        asr=FunAsrAdapter(settings),
        llm=OpenAiLlmAdapter(settings),
        tts=PaddlePhonemeTtsAdapter(settings),
    )
