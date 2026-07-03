"""tool 兜底过渡 TTS。"""
from __future__ import annotations

from deskbot_server.application.tool_interim_tts import build_tool_interim_tts


def test_build_tool_interim_tts_single():
    text = build_tool_interim_tts([{"tool": "websearch", "query": "天气"}])
    assert text == "稍等，我帮你搜一下。"


def test_build_tool_interim_tts_merge_dedupe():
    tools = [
        {"tool": "websearch", "query": "a"},
        {"tool": "capture_camera"},
        {"name": "websearch", "query": "b"},
    ]
    text = build_tool_interim_tts(tools)
    assert "搜一下" in text
    assert "看一下" in text
    assert text.startswith("稍等，")


def test_build_tool_interim_tts_unknown_tool():
    text = build_tool_interim_tts([{"tool": "unknown_xyz"}])
    assert text == "稍等，稍等一下。"


def test_build_tool_interim_tts_empty():
    assert build_tool_interim_tts([]) == ""
