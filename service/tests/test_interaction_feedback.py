from __future__ import annotations

import asyncio
import time


def test_listen_feedback_gaze_when_face_recent():
    from deskbot_server.service.application import interaction_feedback as fb

    fb.clear_face_analysis("dev1")
    fb._listen_last_mono.clear()
    fb.note_face_analysis(
        "dev1",
        {
            "landmarks": [{"name": "nose", "x": 160, "y": 120}],
            "image_w": 320,
            "image_h": 240,
        },
    )
    kind, moves = fb.listen_feedback_moves("dev1")
    assert kind == "gaze"
    assert len(moves) == 1
    assert moves[0]["move"] == "__custom__"
    assert moves[0]["ms"] == fb._MOTION_MS


def test_build_servo_only_pb_payload_no_audio():
    from deskbot_server.service.application import interaction_feedback as fb

    fb.clear_face_analysis("dev1")
    fb.note_face_analysis(
        "dev1",
        {
            "landmarks": [{"name": "nose", "x": 160, "y": 120}],
            "image_w": 320,
            "image_h": 240,
        },
    )
    _kind, moves = fb.listen_feedback_moves("dev1")
    built = fb.build_servo_only_pb_payload(moves, device_id="dev1", request_id="abc123")
    assert built is not None
    payload, req_id = built
    assert req_id == "abc123"
    assert payload["type"] == "pb_single"
    assert payload.get("audio") is None
    assert len(payload["servo"]) >= 1
    assert payload["chunk_ms"] > 0


def test_llm_wait_nod_is_one_short_nod():
    from deskbot_server.service.application import interaction_feedback as fb

    moves = fb.llm_wait_nod_moves()
    assert moves == [{"move": "nod_head", "ms": fb._LLM_WAIT_NOD_MS}]
    built = fb.build_servo_only_pb_payload(moves, device_id="dev-nod", request_id="nod")
    assert built is not None
    payload, _ = built
    assert len(payload["servo"]) == 3
    assert payload["chunk_ms"] == fb._LLM_WAIT_NOD_MS


def test_camera_face_follow_sends_latest_absolute_position(monkeypatch):
    from deskbot_server.service.application import camera_servo_follower as follower

    class Hub:
        pipeline_broker = None

        def __init__(self):
            self.payloads: list[dict] = []

        async def send(self, _device_id, payload):
            self.payloads.append(payload)
            return 1

    async def _run() -> None:
        follower._device_state.clear()
        monkeypatch.setattr(follower, "get_asr_voice_auto_reply_enabled", lambda: True)
        monkeypatch.setattr(follower, "get_camera_servo_auto_mode", lambda: "follow")
        hub = Hub()
        await follower.camera_servo_follower_tick(
            hub,  # type: ignore[arg-type]
            "dev-follow",
            {"landmarks": [{"name": "nose", "x": 240, "y": 120}], "image_w": 320, "image_h": 240},
        )
        assert len(hub.payloads) == 1
        payload = hub.payloads[0]
        assert payload["action"] == "replace"
        assert payload["level"] == 3
        assert payload["servo"][0]["xm"] == 0
        assert payload["servo"][0]["ym"] == 0

    asyncio.run(_run())


def test_listen_feedback_patrol_without_face():
    from deskbot_server.service.application import interaction_feedback as fb

    fb.clear_face_analysis("dev2")
    kind, moves = fb.listen_feedback_moves("dev2")
    assert kind == "patrol"
    assert [m["move"] for m in moves] == ["look_left", "center", "look_right", "center"]
    assert sum(m["ms"] for m in moves) == fb._MOTION_MS


def test_listen_feedback_respects_min_gap(monkeypatch):
    from deskbot_server.service.application import interaction_feedback as fb
    from deskbot_server.ws.asr_chat_hub import AsrChatHub

    async def _run() -> None:
        fb._listen_last_mono.clear()
        sent: list[str] = []

        async def fake_send(*_a, **_k):
            sent.append("ok")
            return 1

        monkeypatch.setattr(fb, "_send_servo_moves", fake_send)
        monkeypatch.setattr("deskbot_server.auto_reply.get_asr_voice_auto_reply_enabled", lambda: True)

        hub = AsrChatHub(device_pb_only=True)
        hub.first_ws = lambda _dev: asyncio.sleep(0, result=object())  # type: ignore[method-assign]

        await fb.maybe_send_listen_feedback(hub, "dev3")
        await fb.maybe_send_listen_feedback(hub, "dev3")
        assert len(sent) == 1

        fb._listen_last_mono["dev3"] = time.monotonic() - fb._LISTEN_MIN_GAP_SEC - 0.1
        await fb.maybe_send_listen_feedback(hub, "dev3")
        assert len(sent) == 2

    asyncio.run(_run())


def test_llm_wait_nod_loop_stops_when_done(monkeypatch):
    from deskbot_server.service.application import interaction_feedback as fb
    from deskbot_server.ws.asr_chat_hub import AsrChatHub

    async def _run() -> None:
        calls: list[int] = []

        async def fake_send(*_a, **_k):
            calls.append(1)
            return 1

        monkeypatch.setattr(fb, "_send_servo_moves", fake_send)
        monkeypatch.setattr(fb, "_MOTION_MS", 50)
        monkeypatch.setattr("deskbot_server.auto_reply.get_asr_voice_auto_reply_enabled", lambda: True)

        hub = AsrChatHub(device_pb_only=True)
        hub.first_ws = lambda _dev: asyncio.sleep(0, result=object())  # type: ignore[method-assign]

        done = asyncio.Event()
        task = asyncio.create_task(fb.llm_wait_nod_feedback_loop(hub, "dev4", done))
        await asyncio.sleep(0.05)
        assert calls
        done.set()
        await fb.stop_llm_wait_nod_feedback(done, task)

    asyncio.run(_run())
