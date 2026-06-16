"""text_split 单元测试。"""
from __future__ import annotations

from deskbot_server.tts.text_split import split_tts_by_punctuation


def test_split_single_sentence():
    assert split_tts_by_punctuation("你好") == ["你好"]
    assert split_tts_by_punctuation("你好。") == ["你好。"]


def test_split_by_commas_and_periods():
    assert split_tts_by_punctuation("你好，我是小歪。") == ["你好，", "我是小歪。"]
    assert split_tts_by_punctuation("好的！今天不错？") == ["好的！", "今天不错？"]


def test_split_empty():
    assert split_tts_by_punctuation("") == []
    assert split_tts_by_punctuation("   ") == []
