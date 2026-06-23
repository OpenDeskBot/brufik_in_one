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
