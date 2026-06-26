"""doubao_phoneme_align 单元测试。"""
from __future__ import annotations

from deskbot_server.core.ports.tts import PhonemeSegment
from deskbot_server.tts.doubao_phoneme_align import (
    build_phoneme_segments,
    extract_timed_phonemes_from_sentence_end,
    split_pcm_by_timed_phonemes,
    TimedPhoneme,
)


def test_split_pcm_by_api_phonemes():
    sr = 16000
    pcm = b"\x01\x02" * (sr // 10)  # 100ms
    timed = [
        TimedPhoneme("n", 0, 40),
        TimedPhoneme("i", 40, 100),
    ]
    segs = split_pcm_by_timed_phonemes(pcm, sr, timed)
    assert len(segs) == 2
    assert sum(len(s.pcm) for s in segs) == len(pcm)
    assert segs[0].phoneme == "n"
    assert segs[1].phoneme == "i"


def test_extract_words_from_sentence_end():
    payload = {
        "text": "你好",
        "words": [
            {"word": "你", "start_time": 0.0, "end_time": 0.2},
            {"word": "好", "start_time": 0.2, "end_time": 0.4},
        ],
    }
    timed = extract_timed_phonemes_from_sentence_end(payload, text="你好")
    assert len(timed) >= 2


def test_pinyin_fallback_segments():
    sr = 24000
    pcm = b"\x00\x01" * (sr // 5)  # 200ms
    segs = build_phoneme_segments(text="你好", pcm=pcm, sample_rate=sr, sentence_end={"words": []})
    assert len(segs) >= 2
    assert isinstance(segs[0], PhonemeSegment)
    assert sum(len(s.pcm) for s in segs) == len(pcm)


def test_sentence_end_preferred_over_incremental_subtitles():
    """增量 subtitle 不应叠加到 sentence_end 之上导致末尾重复。"""
    sr = 24000
    pcm = b"\x00\x01" * (sr // 2)  # 500ms
    words = [
        {"word": "你", "startTime": 0.0, "endTime": 0.25},
        {"word": "好", "startTime": 0.25, "endTime": 0.5},
    ]
    subtitles = [
        {"words": [words[0]]},
        {"words": words},
    ]
    segs = build_phoneme_segments(
        text="你好",
        pcm=pcm,
        sample_rate=sr,
        sentence_end={"words": words},
        subtitles=subtitles,
    )
    assert sum(len(s.pcm) for s in segs) == len(pcm)
    assert len(segs) >= 2


def test_split_pcm_no_duplicate_on_overlapping_units():
    """口型时间窗重叠时，PCM 每字节只能落在一个分片里。"""
    sr = 24000
    total_ms = 3000
    pcm = b"\x01\x02" * (sr * total_ms // 1000)
    units = [
        TimedPhoneme("a", 0, 1500),
        TimedPhoneme("i", 1200, 1800),
        TimedPhoneme("o", 1700, 2500),
        TimedPhoneme("u", 2400, 3000),
    ]
    segs = split_pcm_by_timed_phonemes(pcm, sr, units)
    assert sum(len(s.pcm) for s in segs) == len(pcm)
    assert sum(s.ms for s in segs) == total_ms
