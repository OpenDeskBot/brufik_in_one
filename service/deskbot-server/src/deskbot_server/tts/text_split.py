"""TTS 文案按句末标点分句，用于分段合成与下发。"""
from __future__ import annotations

import re

# 仅句末标点：。！？!? 及换行；逗号/分号/顿号不切分
_TTS_SENTENCE_END_RE = re.compile(r"[^。！？!?\n]+[。！？!?]?")


def split_tts_by_punctuation(text: str) -> list[str]:
    """按句末标点切分 TTS 文本，每段含末尾标点（若有）。

    逗号、分号、顿号不切分；无句末标点时返回整句单段；空文本返回 []。
    """
    s = str(text or "").strip()
    if not s:
        return []
    parts = _TTS_SENTENCE_END_RE.findall(s)
    chunks = [p.strip() for p in parts if p.strip()]
    return chunks if chunks else [s]
