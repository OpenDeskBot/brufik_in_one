from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatTurnResult:
    """一轮 ASR→LLM→TTS/pb 的时序与结果摘要。"""

    llm_text: str | None = None
    llm_raw: str | None = None
    moves: list[Any] = field(default_factory=list)
    anims: list[Any] = field(default_factory=list)
    tools: list[Any] = field(default_factory=list)
    tool_results: list[Any] = field(default_factory=list)
    servo: list[Any] = field(default_factory=list)
    need_reply: bool = True
    json_ok: bool = False
    t_llm_end: float | None = None
    t_tts_synth_end: float | None = None
    t_tts_end: float | None = None
    status: str = "ok"
    error: str | None = None
    voice_auto_reply_off: bool = False
    scenes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)
