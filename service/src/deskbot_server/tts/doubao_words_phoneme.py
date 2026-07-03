"""豆包 ``TTSSentenceEnd.words`` → 口型音素时间轴（英文 G2P + 中文拼音 + ARPA→口型键）。

输入（豆包实际字段名，秒级 ``startTime`` / ``endTime``）::

    [
        {"word": "Hello", "startTime": 0.295, "endTime": 0.855, "confidence": 0.88},
        {"word": "world", "startTime": 0.855, "endTime": 1.415, "confidence": 0.92},
    ]

输出为 PB 管线使用的分片 dict：``{"phoneme": "h", "ms": 112}``（``pcm`` 由上层按时间轴切分后填入）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from deskbot_server.pb.shapes import simplify_phoneme_key

logger = logging.getLogger("deskbot-server")

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_EN_WORD_RE = re.compile(r"[A-Za-z]+")
_PUNCT_PAUSE = frozenset("，。！？!?、；;：:…")

_INITIALS = (
    "zh",
    "ch",
    "sh",
    "b",
    "p",
    "m",
    "f",
    "d",
    "t",
    "n",
    "l",
    "g",
    "k",
    "h",
    "r",
    "z",
    "c",
    "s",
    "j",
    "q",
    "x",
    "y",
    "w",
)

_VOWEL_PHONES = frozenset(
    {
        "AA",
        "AE",
        "AH",
        "AO",
        "AW",
        "AY",
        "EH",
        "ER",
        "EY",
        "IH",
        "IY",
        "OW",
        "OY",
        "UH",
        "UW",
    }
)

# ARPAbet → ``deskbot-face.json`` phonemes 已有中文口型键
_ARPA_TO_MOUTH: dict[str, str] = {
    "AA": "a",
    "AE": "a",
    "AH": "a",
    "AO": "o",
    "AW": "a",
    "AY": "ai",
    "EH": "e",
    "ER": "e",
    "EY": "ei",
    "IH": "i",
    "IY": "i",
    "OW": "o",
    "OY": "ou",
    "UH": "u",
    "UW": "u",
    "B": "b",
    "CH": "ch",
    "D": "d",
    "DH": "d",
    "F": "f",
    "G": "g",
    "HH": "h",
    "JH": "j",
    "K": "g",
    "L": "l",
    "M": "m",
    "N": "n",
    "NG": "ng",
    "P": "b",
    "R": "r",
    "S": "s",
    "SH": "sh",
    "T": "d",
    "TH": "s",
    "V": "f",
    "W": "w",
    "Y": "y",
    "Z": "s",
    "ZH": "sh",
}

_g2p: Any | None = None


@dataclass(frozen=True)
class PhonemeTiming:
    """单个口型音素片的时间轴（毫秒）。"""

    phoneme: str
    start_ms: int
    end_ms: int
    word: str = ""

    @property
    def ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


def _ms_from_api_time(value: Any) -> int:
    """API 时间戳可能是秒（float）或毫秒（int）。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0
    if v <= 0:
        return 0
    if v < 100:
        return int(round(v * 1000))
    return int(round(v))


def _word_time_bounds(item: dict[str, Any]) -> tuple[str, int, int]:
    word = str(item.get("word") or item.get("text") or "").strip()
    start = _ms_from_api_time(
        item.get("startTime", item.get("start_time", item.get("start", item.get("start_ms", 0))))
    )
    end = _ms_from_api_time(
        item.get("endTime", item.get("end_time", item.get("end", item.get("end_ms", 0))))
    )
    if end <= start:
        end = start + 80
    return word, start, end


def _get_g2p():
    global _g2p
    if _g2p is None:
        from g2p_en import G2p

        _g2p = G2p()
    return _g2p


def _strip_arpa_stress(raw: str) -> str:
    s = str(raw or "").strip()
    while len(s) >= 2 and s[-1].isdigit():
        s = s[:-1]
    return s


def map_arpa_to_mouth_phone(raw: str) -> str:
    """ARPAbet / 字母 → 口型查表键（中文拼音键优先）。"""
    base = _strip_arpa_stress(str(raw or ""))
    key = simplify_phoneme_key(base)
    if not key or key == "_":
        return "_"
    mapped = _ARPA_TO_MOUTH.get(key.upper())
    if mapped:
        return mapped
    if len(key) == 1 and key.isalpha():
        return key.lower()
    return key.lower()


def _split_pinyin_syllable(syllable: str) -> tuple[str, str]:
    s = str(syllable or "").strip().lower()
    if not s:
        return "_", "_"
    if s[0].isdigit():
        s = s[1:]
    while s and s[-1].isdigit():
        s = s[:-1]
    if not s or s in ("sil", "sp"):
        return "_", "_"
    ini = ""
    for cand in sorted(_INITIALS, key=len, reverse=True):
        if s.startswith(cand):
            ini = cand
            s = s[len(cand) :]
            break
    fin = s or ini or "_"
    if ini == fin:
        ini = ""
    return simplify_phoneme_key(ini or "_"), simplify_phoneme_key(fin)


def _pinyin_for_char(ch: str) -> list[tuple[str, float]]:
    try:
        from pypinyin import Style, pinyin
    except ImportError:
        return [("_", 1.0)]
    rows = pinyin(ch, style=Style.TONE3, errors="ignore")
    if not rows or not rows[0] or not rows[0][0]:
        return [("_", 1.0)]
    ini, fin = _split_pinyin_syllable(rows[0][0])
    units: list[tuple[str, float]] = []
    if ini and ini != "_":
        units.append((ini, 0.35))
    if fin and fin != "_":
        units.append((fin, 0.65))
    return units or [("_", 1.0)]


def _english_phoneme_units(word: str) -> list[tuple[str, float]]:
    phones: list[tuple[str, float]] = []
    try:
        raw_phones = _get_g2p()(str(word or "").lower())
    except Exception as exc:
        logger.warning("[TTS/doubao_words] G2P 失败 word=%r err=%s", word, exc)
        raw_phones = []
    for raw in raw_phones:
        mouth = map_arpa_to_mouth_phone(str(raw))
        if mouth == "_":
            continue
        weight = 0.65 if _strip_arpa_stress(str(raw)).upper() in _VOWEL_PHONES else 0.35
        phones.append((mouth, weight))
    if phones:
        return phones
    w = str(word or "").strip()
    if w and w[0].isalpha():
        return [(map_arpa_to_mouth_phone(w[0]), 1.0)]
    return [("_", 1.0)]


def mouth_units_for_text(text: str) -> list[tuple[str, float]]:
    """整段文本 → 加权口型单元（无时间轴，供 fallback 均分 PCM）。"""
    units: list[tuple[str, float]] = []
    s = str(text or "")
    i = 0
    while i < len(s):
        m = _EN_WORD_RE.match(s, i)
        if m:
            units.extend(_english_phoneme_units(m.group(0)))
            i = m.end()
            continue
        ch = s[i]
        i += 1
        if ch in _PUNCT_PAUSE:
            units.append(("_", 0.25))
        elif _CJK_RE.match(ch):
            units.extend(_pinyin_for_char(ch))
    return units


def mouth_units_for_word(word: str) -> list[tuple[str, float]]:
    """单个 ``words`` 条目内的文本 → 加权口型单元。"""
    if not word:
        return []
    if len(word) == 1 and word in _PUNCT_PAUSE:
        return [("_", 0.25)]
    if _EN_WORD_RE.fullmatch(word):
        return _english_phoneme_units(word)
    if len(word) == 1 and _CJK_RE.match(word):
        return _pinyin_for_char(word)
    return mouth_units_for_text(word)


def _distribute_word_window(
    word: str,
    weighted: list[tuple[str, float]],
    *,
    start_ms: int,
    end_ms: int,
) -> list[PhonemeTiming]:
    if not weighted:
        return [PhonemeTiming(phoneme="", start_ms=start_ms, end_ms=end_ms, word=word)]
    total_w = sum(w for _, w in weighted) or 1.0
    dur = max(1, end_ms - start_ms)
    out: list[PhonemeTiming] = []
    cursor = start_ms
    for i, (ph, w) in enumerate(weighted):
        if i == len(weighted) - 1:
            seg_end = end_ms
        else:
            seg_end = start_ms + int(dur * (sum(x[1] for x in weighted[: i + 1]) / total_w))
        seg_end = max(seg_end, cursor + 20)
        seg_end = min(seg_end, end_ms)
        mouth = "" if ph == "_" else str(ph)
        out.append(PhonemeTiming(phoneme=mouth, start_ms=cursor, end_ms=seg_end, word=word))
        cursor = seg_end
    return out


def align_doubao_words(words: list[Any]) -> list[PhonemeTiming]:
    """豆包 ``words`` 列表 → 带毫秒时间轴的口型音素序列。"""
    out: list[PhonemeTiming] = []
    for item in words or []:
        if not isinstance(item, dict):
            continue
        word, start_ms, end_ms = _word_time_bounds(item)
        if not word:
            continue
        weighted = mouth_units_for_word(word)
        if not weighted:
            out.append(PhonemeTiming(phoneme="", start_ms=start_ms, end_ms=end_ms, word=word))
            continue
        out.extend(
            _distribute_word_window(word, weighted, start_ms=start_ms, end_ms=end_ms)
        )
    return out


def phoneme_timings_to_pb_segments(
    timings: list[PhonemeTiming],
    *,
    include_empty_phoneme: bool = False,
) -> list[dict[str, Any]]:
    """``PhonemeTiming`` → PB 分片 dict（``phoneme`` + ``ms``，无 ``pcm``）。"""
    segs: list[dict[str, Any]] = []
    for t in timings:
        if t.ms <= 0:
            continue
        if not t.phoneme and not include_empty_phoneme:
            continue
        row: dict[str, Any] = {"phoneme": t.phoneme, "ms": t.ms}
        if t.word:
            row["word"] = t.word
        segs.append(row)
    return segs


def doubao_words_to_pb_segments(words: list[Any]) -> list[dict[str, Any]]:
    """豆包 ``words`` → PB 下发用 ``[{"phoneme", "ms"}, ...]``。"""
    return phoneme_timings_to_pb_segments(align_doubao_words(words))
