"""boot_wake 开机苏醒场景下发。"""
from __future__ import annotations

import asyncio


def test_deliver_boot_wake_scene_finds_wake(monkeypatch):
    from deskbot_server.application import boot_wake

    sent: list = []

    class FakeHub:
        pipeline_broker = None

        async def send_pb_chain_ordered(self, device_id, frames, binaries_per_frame=None):
            sent.append((device_id, frames, binaries_per_frame))
            return 1

    async def fake_publish(*_a, **_k):
        return None

    monkeypatch.setattr(boot_wake, "publish_auto_dispatch_event", fake_publish)
    monkeypatch.setattr(
        boot_wake,
        "load_face_expr_scenes_file",
        lambda **_: [{"name": "wake", "title": "苏醒", "frames": [{"ms": 100, "elements": {}}]}],
    )
    monkeypatch.setattr(
        boot_wake,
        "design_frames_to_pb_chain",
        lambda frames, **_: [({"type": "pb_single"}, [])],
    )
    monkeypatch.setattr(boot_wake, "attach_pb_device_hints_from_config", lambda frames: None)

    n = asyncio.run(boot_wake.deliver_boot_wake_scene(FakeHub(), "deskbot_test"))
    assert n == 1
    assert sent[0][0] == "deskbot_test"
    assert sent[0][1][0]["action"] == "replace"
    assert sent[0][1][0]["level"] == 1


def test_deliver_boot_wake_scene_missing_scene(monkeypatch):
    from deskbot_server.application import boot_wake

    monkeypatch.setattr(boot_wake, "load_face_expr_scenes_file", lambda **_: [])

    n = asyncio.run(boot_wake.deliver_boot_wake_scene(object(), "deskbot_test"))
    assert n == 0
