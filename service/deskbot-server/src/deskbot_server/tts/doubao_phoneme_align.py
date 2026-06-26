"""豆包 TTS 时间戳 → 音素分片（口型 / pb 交错用）。

优先使用 ``TTSSentenceEnd`` / ``TTSSubtitle`` 中的 ``words`` / ``phonemes``；
若 API 未返回（seed-tts-2.0 常见），则按文本拼音均分 PCM 时长作近似口型。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from deskbot_server.core.ports.tts import PhonemeSegment
from deskbot_server.pb.shapes import simplify_phoneme_key
from deskbot_server.tts.doubao_words_phoneme import align_doubao_words, mouth_units_for_text

logger = logging.getLogger("deskbot-server")


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
    """字/词级时间戳 → 口型音素片（英文 G2P + 中文拼音，见 ``doubao_words_phoneme``）。"""
    del text
    timings = align_doubao_words(items)
    if not timings:
        return []
    return [
        TimedPhoneme(
            phoneme=t.phoneme if t.phoneme else "_",
            start_ms=t.start_ms,
            end_ms=t.end_ms,
        )
        for t in timings
    ]


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
    """豆包 ``TTSSubtitle`` 常为增量推送；只取**最后一条**含时间戳的 payload，避免口型/PCM 切分重复。"""
    for item in reversed(subtitles or []):
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("phonemes"), list):
            parsed = _parse_api_phoneme_list(item["phonemes"])
            if parsed:
                return parsed
        if isinstance(item.get("words"), list):
            parsed = _parse_api_word_list(item["words"], text=str(item.get("text") or ""))
            if parsed:
                return parsed
    return []


def _dedupe_timed_phonemes(units: list[TimedPhoneme]) -> list[TimedPhoneme]:
    """去掉相同起止时间的重复片（增量 subtitle 合并后偶发）。"""
    if not units:
        return []
    seen: set[tuple[int, int, str]] = set()
    out: list[TimedPhoneme] = []
    for u in sorted(units, key=lambda x: (x.start_ms, x.end_ms, x.phoneme)):
        key = (u.start_ms, u.end_ms, u.phoneme)
        if key in seen:
            continue
        seen.add(key)
        out.append(u)
    return out


def _pinyin_timed_units_proportional(text: str, *, total_ms: int) -> list[TimedPhoneme]:
    weighted = mouth_units_for_text(text)
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
    """按时间戳切 s16le mono PCM；保证每段 PCM **不重叠** 且合计覆盖整段音频。"""
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

    n = len(units)
    cuts_ms = [0]
    for i, u in enumerate(units[:-1]):
        end_ms = int(round(u.end_ms * scale))
        end_ms = max(end_ms, cuts_ms[-1] + 15)
        end_ms = min(end_ms, total_ms)
        cuts_ms.append(end_ms)
    cuts_ms.append(total_ms)

    segs: list[PhonemeSegment] = []
    for i, u in enumerate(units):
        start_ms = cuts_ms[i]
        end_ms = cuts_ms[i + 1]
        if end_ms <= start_ms:
            continue
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

    # 舍入误差：仅补末尾未覆盖的采样，不叠加到已有 PCM 上造成重复
    used = sum(len(s.pcm) for s in segs)
    if used < len(pcm):
        tail = pcm[used:]
        if tail and len(segs) == 1:
            last = segs[-1]
            segs[-1] = PhonemeSegment(
                phoneme=last.phoneme,
                ms=last.ms + pcm_duration_ms(tail, sample_rate),
                pcm=last.pcm + tail,
            )
        elif tail:
            segs.append(
                PhonemeSegment(
                    phoneme=segs[-1].phoneme,
                    ms=pcm_duration_ms(tail, sample_rate),
                    pcm=tail,
                )
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
    timed: list[TimedPhoneme] = []
    if sentence_end:
        timed = extract_timed_phonemes_from_sentence_end(sentence_end, text=text)
    if not timed:
        timed = extract_timed_phonemes_from_subtitles(subtitles or [])
    timed = _dedupe_timed_phonemes(timed)
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
