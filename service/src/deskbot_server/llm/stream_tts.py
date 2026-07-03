"""从 LLM JSON 流式输出中尽早提取 ``tts`` 字段。"""
from __future__ import annotations

import json
import re
from typing import Callable, Optional

_TTS_KEY_RE = re.compile(r'"tts"\s*:\s*', re.IGNORECASE)


def try_extract_tts_from_partial_json(buf: str) -> tuple[Optional[str], bool]:
    """尝试从部分 JSON 文本中提取 ``tts`` 字符串值。

    返回 ``(value, complete)``：
    - 尚未出现 ``tts`` 键或未闭合字符串：``(None, False)``
    - 已闭合：``(value, True)``，空字符串表示 ``"tts":""``
    """
    text = (buf or "").lstrip()
    if text.startswith("```"):
        nl = text.find("\n")
        if nl >= 0:
            text = text[nl + 1 :].lstrip()

    m = _TTS_KEY_RE.search(text)
    if not m:
        return None, False

    i = m.end()
    if i >= len(text):
        return None, False

    rest = text[i:].lstrip()
    if rest.startswith("null"):
        return "", True

    if not rest.startswith('"'):
        return None, False

    raw, end = _read_json_string(rest, start=0)
    if end < 0:
        return None, False
    return raw, True


def _read_json_string(text: str, *, start: int) -> tuple[str, int]:
    """读取以 ``"`` 开头的 JSON 字符串，返回解码值与结束索引（不含）。"""
    if start >= len(text) or text[start] != '"':
        return "", -1

    i = start + 1
    chunks: list[str] = []
    while i < len(text):
        ch = text[i]
        if ch == '"':
            try:
                return json.loads(text[start : i + 1]), i + 1
            except json.JSONDecodeError:
                return "", -1
        if ch == "\\":
            if i + 1 >= len(text):
                return "", -1
            i += 2
            continue
        i += 1
    return "", -1


class JsonTtsStreamExtractor:
    """累积流式 delta，在 ``tts`` 字符串闭合时回调一次。"""

    def __init__(
        self,
        on_tts_ready: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._buf = ""
        self._fired = False
        self._on_tts_ready = on_tts_ready

    @property
    def buffer(self) -> str:
        return self._buf

    def feed(self, chunk: str) -> Optional[str]:
        if self._fired or not chunk:
            return None
        self._buf += chunk
        value, complete = try_extract_tts_from_partial_json(self._buf)
        if not complete:
            return None
        self._fired = True
        text = (value or "").strip()
        if text and self._on_tts_ready is not None:
            self._on_tts_ready(text)
        return text or None

    def reset(self) -> None:
        self._buf = ""
        self._fired = False


__all__ = [
    "JsonTtsStreamExtractor",
    "try_extract_tts_from_partial_json",
]
