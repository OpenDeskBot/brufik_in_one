"""ASR 识别结果是否进入对话的文本过滤。"""

from __future__ import annotations

import re
import unicodedata

# 去标点后再计长度；保留中文、字母、数字
_CONTENT_CHAR_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")


def asr_text_without_punctuation(text: str) -> str:
    """去掉空白与 Unicode 标点，只保留中英数字符。"""
    s = "".join(str(text or "").split())
    out: list[str] = []
    for ch in s:
        cat = unicodedata.category(ch)
        if cat.startswith(("P", "Z")):
            continue
        if _CONTENT_CHAR_RE.fullmatch(ch):
            out.append(ch)
    return "".join(out)


def is_asr_text_acceptable(
    text: str,
    *,
    min_len: int = 2,
    min_chinese_ratio: float = 0.0,
) -> bool:
    """去标点后有效长度 ``>= min_len``；可选中文占比下限。"""
    cleaned = asr_text_without_punctuation(text)
    if len(cleaned) < max(1, int(min_len)):
        return False
    if min_chinese_ratio <= 0:
        return True
    zh_count = len(re.findall(r"[\u4e00-\u9fff]", cleaned))
    return zh_count / max(1, len(cleaned)) >= float(min_chinese_ratio)
