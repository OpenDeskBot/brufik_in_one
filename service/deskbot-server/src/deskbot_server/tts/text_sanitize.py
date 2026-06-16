"""PaddleSpeech 中文 TTS 入参清洗（避免 frontend G2P / tone_sandhi 空列表崩溃）。"""

from __future__ import annotations

import re

# ``嗯...我觉得`` 等会让 g2p 产生空 finals → tone_sandhi IndexError
_ELLIPSIS_RE = re.compile(r"\.{2,}|…+")
# 波浪号等装饰符非中文标点，易触发异常
_TILDE_RE = re.compile(r"[~～]+")
# 连续标点压成单个逗号
_PUNCT_RUN_RE = re.compile(r"[,，.。!！?？;；:：、]{2,}")
# 孤立 UTF-16 代理项无法 UTF-8 编码，会导致 PaddleSpeech G2P 崩溃
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def sanitize_tts_text_for_paddlespeech(text: str, *, lang: str = "zh") -> str:
    """清洗 LLM/用户文案后再送 ``streaming_phoneme``。"""
    s = str(text or "").strip()
    if not s:
        return s
    s = _SURROGATE_RE.sub("", s).strip()
    if not s:
        return "。"
    s = _ELLIPSIS_RE.sub("，", s)
    s = _TILDE_RE.sub("", s)
    s = _PUNCT_RUN_RE.sub("，", s)
    if str(lang).lower() == "mix":
        s = re.sub(r"[ \t]+", " ", s)
        return s.strip() or "。"
    s = re.sub(r"\s+", "", s)
    return s.strip("，。,. ") or s
