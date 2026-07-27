"""LiveService 单元测试。"""

from __future__ import annotations


def test_wander_moves_timing(monkeypatch):
    from deskbot_server.service import live_service

    monkeypatch.setattr(live_service, "_resolve_servo_preset_steps", lambda *a, **k: [])
    moves = live_service.wander_moves("dev1")
    assert moves[0] == {"move": "look_left", "ms": 1000}
    assert moves[1] == {"move": "look_left", "ms": 2000}
    assert moves[2] == {"move": "look_right", "ms": 2000}
    assert moves[4]["move"] == "center"
    assert 1000 <= moves[4]["ms"] <= 3000


def test_sleep_moves_includes_hold(monkeypatch):
    from deskbot_server.service import live_service

    monkeypatch.setattr(live_service, "_resolve_servo_preset_steps", lambda *a, **k: [])
    moves = live_service.sleep_moves("dev1", hold_ms=28000)
    assert moves[0]["move"] == "center"
    assert moves[1]["move"] == "look_down"
    assert moves[2] == {"move": "look_down", "ms": 28000}


def test_conversation_blocks_live_idle():
    from deskbot_server.service.live_service import LiveService

    LiveService.reset_instance()
    svc = LiveService()

    class Hub:
        pipeline_broker = None

    svc.bind(Hub())
    svc.note_conversation_start("dev1")
    assert svc._ensure("dev1").in_conversation
    assert svc.handles_idle("dev1") is False
    svc.note_conversation_end("dev1")
    assert not svc._ensure("dev1").in_conversation
    assert svc.cooldown_remaining("dev1") > 0
    LiveService.reset_instance()


def test_face_lost_clears_tracking():
    from deskbot_server.service.live_service import LiveService, LiveState

    LiveService.reset_instance()
    svc = LiveService()
    st = svc._ensure("dev1")
    st.mode = LiveState.GAZE
    svc.on_face_lost("dev1")
    assert st.mode == LiveState.WANDER
    LiveService.reset_instance()


def test_note_conversation_end_resets_to_wander():
    from deskbot_server.service.live_service import LiveService, LiveState

    LiveService.reset_instance()
    svc = LiveService()
    st = svc._ensure("dev1")
    st.mode = LiveState.SLEEP
    svc.note_conversation_end("dev1")
    assert st.mode == LiveState.WANDER
    LiveService.reset_instance()
