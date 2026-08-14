"""Composition Root：装配 ChatService，并绑定 MVC Service 单例。"""

from __future__ import annotations

from deskbot_server.infrastructure.asr.funasr import FunAsrAdapter
from deskbot_server.infrastructure.llm.openai_compat import OpenAiLlmAdapter
from deskbot_server.infrastructure.tts.factory import build_tts_adapter
from deskbot_server.model.settings import AppSettings
from deskbot_server.service.application.chat_service import ChatService
from deskbot_server.service.asr_service import AsrService
from deskbot_server.service.llm_service import LlmService
from deskbot_server.service.tts_service import TtsService


def build_chat_service(config: dict) -> ChatService:
    settings = AppSettings.from_config(config)
    asr = FunAsrAdapter(settings)
    llm = OpenAiLlmAdapter(settings)
    tts = build_tts_adapter(settings)

    AsrService().bind(asr)
    LlmService().bind(llm)
    TtsService().bind(tts)

    return ChatService(settings, asr=asr, llm=llm, tts=tts)
