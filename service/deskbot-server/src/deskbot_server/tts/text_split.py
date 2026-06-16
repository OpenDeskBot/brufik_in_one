"""TTS 文案按标点分句，用于分段合成与下发。"""
from __future__ import annotations

import re

# 中英文常见句读符；保留在上一段末尾
_TTS_PUNCT_SPLIT_RE = re.compile(
    r"[^。！？!?；;，,、\n]+[。！？!?；;，,、]?"
)


def split_tts_by_punctuation(text: str) -> list[str]:
    """按标点切分 TTS 文本，每段含末尾标点（若有）。

    无标点时返回整句单段；空文本返回 []。
    """
    s = str(text or "").strip()
    if not s:
        return []
    parts = _TTS_PUNCT_SPLIT_RE.findall(s)
    chunks = [p.strip() for p in parts if p.strip()]
    return chunks if chunks else [s]
