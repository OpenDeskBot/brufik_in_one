from deskbot_server.infrastructure.asr.funasr import FunAsrAdapter
from deskbot_server.infrastructure.llm.openai_compat import OpenAiLlmAdapter
from deskbot_server.infrastructure.tts.paddle_phoneme import PaddlePhonemeTtsAdapter
from deskbot_server.infrastructure.ws.downlink_adapter import WsDownlinkAdapter

__all__ = [
    "FunAsrAdapter",
    "OpenAiLlmAdapter",
    "PaddlePhonemeTtsAdapter",
    "WsDownlinkAdapter",
]
