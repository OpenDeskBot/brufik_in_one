"""deskbot_server.tts — 文本处理与 TTS 客户端。"""

from deskbot_server.tts.doubao import (
    DoubaoTtsConfig,
    DoubaoTtsResult,
    load_doubao_tts_config,
    synthesize_doubao_tts,
)
from deskbot_server.tts.speakers import (
    list_doubao_tts_speaker_presets,
    suggest_resource_id,
)

__all__ = [
    "DoubaoTtsConfig",
    "DoubaoTtsResult",
    "list_doubao_tts_speaker_presets",
    "load_doubao_tts_config",
    "suggest_resource_id",
    "synthesize_doubao_tts",
]
