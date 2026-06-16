"""TTS 文案清洗单元测试。"""

from __future__ import annotations

from paddlespeech_server.phoneme import sanitize_mix_tts_text, sanitize_zh_tts_text


def test_sanitize_mix_keeps_english_spaces():
    raw = "你好，welcome to OpenDeskBot，很高兴见到你。"
    out = sanitize_mix_tts_text(raw)
    assert "welcome to" in out
    assert "你好" in out


def test_sanitize_zh_strips_spaces():
    raw = "你好 世界"
    out = sanitize_zh_tts_text(raw)
    assert " " not in out
    assert out == "你好世界"


def test_sanitize_zh_strips_lone_surrogates():
    raw = "\udce6\udcb5\udc8b\udce8\udcaf\udc95"
    assert sanitize_zh_tts_text(raw) == "。"


def test_sanitize_mix_strips_embedded_surrogates():
    raw = "hi \udce6 there"
    assert sanitize_mix_tts_text(raw) == "hi there"
