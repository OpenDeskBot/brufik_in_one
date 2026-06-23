"""豆包 TTS 时间戳 → 音素分片（口型 / pb 交错用）。

优先使用 ``TTSSentenceEnd`` / ``TTSSubtitle`` 中的 ``words`` / ``phonemes``；
若 API 未返回（seed-tts-2.0 常见），则按文本拼音均分 PCM 时长作近似口型。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from deskbot_server.core.ports.tts import PhonemeSegment
from deskbot_server.pb.shapes import simplify_phoneme_key

logger = logging.getLogger("deskbot-server")

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
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


@dataclass(frozen=True)
class TimedPhoneme:
    phoneme: str
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
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


def _normalize_api_phoneme(raw: Any) -> str:
    if raw is None:
        return "_"
    if isinstance(raw, dict):
        for key in ("phone", "phoneme", "ph", "symbol"):
            if key in raw and raw[key]:
                return simplify_phoneme_key(str(raw[key]))
        return "_"
    return simplify_phoneme_key(str(raw))


def _parse_api_phoneme_list(items: list[Any]) -> list[TimedPhoneme]:
    out: list[TimedPhoneme] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        ph = _normalize_api_phoneme(it)
        start = _ms_from_api_time(it.get("start_time", it.get("start", it.get("start_ms", 0))))
        end = _ms_from_api_time(it.get("end_time", it.get("end", it.get("end_ms", 0))))
        if end <= start:
            end = start + max(20, int(it.get("duration_ms") or 40))
        out.append(TimedPhoneme(phoneme=ph, start_ms=start, end_ms=end))
    return out


def _parse_api_word_list(items: list[Any], *, text: str) -> list[TimedPhoneme]:
    """字级时间戳 → 按拼音拆成多个口型片。"""
    out: list[TimedPhoneme] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        word = str(it.get("word") or it.get("text") or "").strip()
        start = _ms_from_api_time(it.get("start_time", it.get("start", 0)))
        end = _ms_from_api_time(it.get("end_time", it.get("end", 0)))
        if end <= start:
            end = start + 80
        if not word:
            continue
        if len(word) == 1 and word in _PUNCT_PAUSE:
            out.append(TimedPhoneme("_", start, end))
            continue
        weighted = _pinyin_weighted_units(word)
        if not weighted:
            out.append(TimedPhoneme("_", start, end))
            continue
        total_w = sum(w for _, w in weighted) or 1.0
        dur = max(1, end - start)
        cursor = start
        for i, (ph, w) in enumerate(weighted):
            if i == len(weighted) - 1:
                seg_end = end
            else:
                seg_end = start + int(dur * (sum(x[1] for x in weighted[: i + 1]) / total_w))
            seg_end = max(seg_end, cursor + 20)
            out.append(TimedPhoneme(ph, cursor, min(seg_end, end)))
            cursor = seg_end
    if out:
        return out
    return []


def extract_timed_phonemes_from_sentence_end(payload: dict[str, Any] | None, *, text: str) -> list[TimedPhoneme]:
    if not isinstance(payload, dict):
        return []
    phonemes = payload.get("phonemes")
    if isinstance(phonemes, list) and phonemes:
        parsed = _parse_api_phoneme_list(phonemes)
        if parsed:
            return parsed
    words = payload.get("words")
    if isinstance(words, list) and words:
        parsed = _parse_api_word_list(words, text=str(payload.get("text") or text))
        if parsed:
            return parsed
    return []


def extract_timed_phonemes_from_subtitles(subtitles: list[Any]) -> list[TimedPhoneme]:
    out: list[TimedPhoneme] = []
    for item in subtitles:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("phonemes"), list):
            out.extend(_parse_api_phoneme_list(item["phonemes"]))
        elif isinstance(item.get("words"), list):
            out.extend(_parse_api_word_list(item["words"], text=str(item.get("text") or "")))
    return out


def _split_pinyin_syllable(syllable: str) -> tuple[str, str]:
    """带声调拼音 → (声母, 韵母) 口型键。"""
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


def _pinyin_weighted_units(text: str) -> list[tuple[str, float]]:
    units: list[tuple[str, float]] = []
    for ch in str(text or ""):
        if ch in _PUNCT_PAUSE:
            units.append(("_", 0.25))
        elif _CJK_RE.match(ch):
            units.extend(_pinyin_for_char(ch))
        elif ch.isalpha():
            units.append((simplify_phoneme_key(ch), 0.5))
    return units


def _pinyin_timed_units_proportional(text: str, *, total_ms: int) -> list[TimedPhoneme]:
    weighted = _pinyin_weighted_units(text)
    if not weighted:
        return [TimedPhoneme("_", 0, max(1, total_ms))]
    total_w = sum(w for _, w in weighted) or 1.0
    dur = max(1, total_ms)
    out: list[TimedPhoneme] = []
    cursor = 0
    for i, (ph, w) in enumerate(weighted):
        if i == len(weighted) - 1:
            end = dur
        else:
            end = int(dur * (sum(x[1] for x in weighted[: i + 1]) / total_w))
        end = max(end, cursor + 15)
        out.append(TimedPhoneme(ph, cursor, min(end, dur)))
        cursor = end
    return out


def pcm_duration_ms(pcm: bytes, sample_rate: int) -> int:
    if not pcm or sample_rate <= 0:
        return 0
    return len(pcm) * 1000 // (sample_rate * 2)


def split_pcm_by_timed_phonemes(
    pcm: bytes,
    sample_rate: int,
    timed: list[TimedPhoneme],
) -> list[PhonemeSegment]:
    """按时间戳切 s16le mono PCM；时间轴与整段音频对齐。"""
    if not pcm:
        return []
    total_ms = pcm_duration_ms(pcm, sample_rate)
    if total_ms <= 0:
        return []

    units = [u for u in timed if u.duration_ms > 0]
    if not units:
        return [PhonemeSegment(phoneme="", ms=total_ms, pcm=pcm)]

    api_end = max(u.end_ms for u in units)
    scale = (total_ms / api_end) if api_end > 0 and abs(api_end - total_ms) > 30 else 1.0

    segs: list[PhonemeSegment] = []
    for i, u in enumerate(units):
        start_ms = int(round(u.start_ms * scale))
        end_ms = int(round(u.end_ms * scale)) if i < len(units) - 1 else total_ms
        end_ms = max(end_ms, start_ms + 15)
        end_ms = min(end_ms, total_ms)
        start_b = start_ms * sample_rate * 2 // 1000
        end_b = end_ms * sample_rate * 2 // 1000
        chunk = pcm[start_b:end_b]
        if not chunk:
            continue
        segs.append(
            PhonemeSegment(
                phoneme=u.phoneme if u.phoneme != "_" else "",
                ms=end_ms - start_ms,
                pcm=chunk,
            )
        )

    if not segs:
        return [PhonemeSegment(phoneme="", ms=total_ms, pcm=pcm)]

    # 补齐末尾采样（舍入误差）
    used = sum(len(s.pcm) for s in segs)
    if used < len(pcm):
        tail = pcm[used:]
        if tail:
            last = segs[-1]
            segs[-1] = PhonemeSegment(
                phoneme=last.phoneme,
                ms=last.ms + pcm_duration_ms(tail, sample_rate),
                pcm=last.pcm + tail,
            )
    return segs


def build_phoneme_segments(
    *,
    text: str,
    pcm: bytes,
    sample_rate: int,
    sentence_end: dict[str, Any] | None = None,
    subtitles: list[Any] | None = None,
) -> list[PhonemeSegment]:
    """API 时间戳优先，否则拼音均分 fallback。"""
    timed = extract_timed_phonemes_from_subtitles(subtitles or [])
    if not timed and sentence_end:
        timed = extract_timed_phonemes_from_sentence_end(sentence_end, text=text)
    if timed:
        segs = split_pcm_by_timed_phonemes(pcm, sample_rate, timed)
        if segs and any(s.phoneme for s in segs):
            logger.info(
                "[TTS/doubao] API 时间戳分片 n=%d text=%r",
                len(segs),
                text[:40],
            )
            return segs
    total_ms = pcm_duration_ms(pcm, sample_rate)
    fallback = _pinyin_timed_units_proportional(text, total_ms=total_ms)
    segs = split_pcm_by_timed_phonemes(pcm, sample_rate, fallback)
    logger.info(
        "[TTS/doubao] 拼音均分口型 n=%d total_ms=%d text=%r",
        len(segs),
        total_ms,
        text[:40],
    )
    return segs if segs else [PhonemeSegment(phoneme="", ms=total_ms, pcm=pcm)]
