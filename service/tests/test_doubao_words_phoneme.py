"""doubao_words_phoneme 单元测试。"""
from __future__ import annotations

from deskbot_server.tts.doubao_words_phoneme import (
    align_doubao_words,
    doubao_words_to_pb_segments,
    map_arpa_to_mouth_phone,
    mouth_units_for_word,
)

HELLO_WORLD_WORDS = [
    {"word": "Hello", "startTime": 0.295, "endTime": 0.855, "confidence": 0.8815363},
    {"word": "world", "startTime": 0.855, "endTime": 1.415, "confidence": 0.9237346},
]

NIHAO_WORDS = [
    {"word": "你", "startTime": 0.315, "endTime": 0.535, "confidence": 0.82},
    {"word": "好", "startTime": 0.535, "endTime": 1.045, "confidence": 0.95},
]


def test_map_arpa_to_mouth_phone():
    assert map_arpa_to_mouth_phone("AH0") == "a"
    assert map_arpa_to_mouth_phone("OW1") == "o"
    assert map_arpa_to_mouth_phone("SH") == "sh"


def test_english_word_uses_g2p_not_letters():
    units = mouth_units_for_word("Hello")
    phones = [p for p, _ in units]
    assert phones == ["h", "a", "l", "o"]
    assert len(phones) == 4


def test_align_hello_world_timeline():
    timings = align_doubao_words(HELLO_WORLD_WORDS)
    assert len(timings) == 8
    assert timings[0].phoneme == "h"
    assert timings[0].start_ms == 295
    assert timings[0].end_ms > timings[0].start_ms
    assert timings[-1].end_ms == 1415
    assert sum(t.ms for t in timings) == 1415 - 295


def test_doubao_words_to_pb_segments():
    segs = doubao_words_to_pb_segments(HELLO_WORLD_WORDS)
    assert len(segs) == 8
    assert all("phoneme" in s and "ms" in s for s in segs)
    assert segs[0]["phoneme"] == "h"
    assert sum(int(s["ms"]) for s in segs) == 1120


def test_chinese_word_pinyin_split():
    timings = align_doubao_words(NIHAO_WORDS)
    assert len(timings) >= 4
    assert any(t.phoneme == "n" for t in timings)
    assert any(t.phoneme == "i" for t in timings)
    assert any(t.phoneme == "h" for t in timings)
    assert any(t.phoneme == "ao" for t in timings)


def test_snake_case_time_fields():
    words = [
        {"word": "Hi", "start_time": 0.1, "end_time": 0.4},
    ]
    segs = doubao_words_to_pb_segments(words)
    assert len(segs) == 2
    assert segs[0]["phoneme"] in ("h", "a", "i")
