from __future__ import annotations

import json

from deskbot_server.llm.stream_tts import (
    JsonTtsStreamExtractor,
    try_extract_tts_from_partial_json,
)


def test_try_extract_tts_complete():
    raw = json.dumps({"need_reply": True, "tts": "你好呀", "tools": []}, ensure_ascii=False)
    value, complete = try_extract_tts_from_partial_json(raw)
    assert complete is True
    assert value == "你好呀"


def test_try_extract_tts_partial():
    partial = '{"need_reply":true,"tts":"你好'
    value, complete = try_extract_tts_from_partial_json(partial)
    assert complete is False
    assert value is None


def test_try_extract_tts_empty_string():
    value, complete = try_extract_tts_from_partial_json('{"tts":"","tools":[]}')
    assert complete is True
    assert value == ""


def test_try_extract_tts_escaped():
    raw = '{"tts":"他说\\"你好\\""}'
    value, complete = try_extract_tts_from_partial_json(raw)
    assert complete is True
    assert value == '他说"你好"'


def test_json_tts_stream_extractor_fires_once():
    seen: list[str] = []

    ext = JsonTtsStreamExtractor(on_tts_ready=seen.append)
    assert ext.feed('{"tts":"你') is None
    assert ext.feed('好"}') == "你好"
    assert seen == ["你好"]
    assert ext.feed(',"tools":[]') is None


def test_json_tts_stream_extractor_skips_empty():
    seen: list[str] = []
    ext = JsonTtsStreamExtractor(on_tts_ready=seen.append)
    assert ext.feed('{"tts":""}') is None
    assert seen == []
