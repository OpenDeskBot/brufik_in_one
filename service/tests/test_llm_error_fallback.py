from __future__ import annotations

from deskbot_server.application.llm_error_fallback import (
    build_llm_error_fallback_plan,
    llm_error_fallback_tts,
    llm_error_idle_moves,
    llm_error_playback_anims,
    llm_error_playback_moves,
)


def test_llm_error_fallback_tts_non_empty():
    assert llm_error_fallback_tts()


def test_build_llm_error_fallback_plan():
    plan = build_llm_error_fallback_plan(tts="测试兜底")
    assert plan["tts"] == "测试兜底"
    assert plan["parsed"]["reply"] == "测试兜底"
    assert plan["parsed"]["moves"] == llm_error_playback_moves()
    assert plan["parsed"]["anims"] == llm_error_playback_anims()


def test_llm_error_idle_moves_cycle():
    moves = llm_error_idle_moves()
    assert len(moves) == 3
    assert sum(int(m["ms"]) for m in moves) == 2000
