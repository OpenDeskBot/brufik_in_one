from deskbot_server.tts.text_sanitize import sanitize_tts_text_for_paddlespeech


def test_sanitize_ellipsis_and_tilde():
    assert (
        sanitize_tts_text_for_paddlespeech("嗯...我觉得挺合适的，你放心啦~")
        == "嗯，我觉得挺合适的，你放心啦"
    )


def test_sanitize_unicode_ellipsis():
    assert sanitize_tts_text_for_paddlespeech("好…啊") == "好，啊"


def test_sanitize_mix_preserves_english_spaces():
    raw = "你好，welcome to bot，再见"
    assert "welcome to" in sanitize_tts_text_for_paddlespeech(raw, lang="mix")


def test_sanitize_strips_lone_surrogates():
    raw = "\udce6\udcb5\udc8b\udce8\udcaf\udc95"
    assert sanitize_tts_text_for_paddlespeech(raw) == "。"


def test_sanitize_strips_embedded_surrogates():
    raw = "你好\udce6世界"
    assert sanitize_tts_text_for_paddlespeech(raw) == "你好世界"
