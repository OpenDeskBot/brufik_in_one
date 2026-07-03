from __future__ import annotations

import asyncio
import threading
import time

import pytest

from deskbot_server.device_camera_frame_store import (
    capture_camera_for_device_async,
    get_device_camera_frame,
    update_device_camera_frame,
    wait_for_device_camera_frame,
)
from deskbot_server.pb.cam_signal import build_cam_fps_signal_pb
from deskbot_server.pb.servo_pcm import make_anim_item, parse_pb_cam_fps, pb_json_messages
from deskbot_server.llm.utils import parse_llm_reply


def _fake_jpeg() -> bytes:
    return b"\xff\xd8\xff\xe0" + b"\x00" * 16 + b"\xff\xd9"


def test_wait_for_device_camera_frame_notifies():
    dev = "dev_wait_cam"
    after = time.time()

    def _late_update():
        time.sleep(0.05)
        update_device_camera_frame(dev, _fake_jpeg())

    threading.Thread(target=_late_update, daemon=True).start()
    row = wait_for_device_camera_frame(dev, after_ts=after, timeout=1.0)
    assert row is not None
    assert row["jpeg"].startswith(b"\xff\xd8")


def test_capture_camera_for_device_async_uses_fresh_cache():
    dev = "dev_async_cam"
    update_device_camera_frame(dev, _fake_jpeg())

    async def _run():
        return await capture_camera_for_device_async(dev, hub=None, wait_timeout_s=0.5)

    cap = asyncio.run(_run())
    assert cap["ok"] is True
    assert cap["jpeg_bytes"] > 0


def test_parse_llm_reply_cam_fps():
    parsed = parse_llm_reply('{"tts":"好","cam_fps":5,"tools":[]}')
    assert parsed["json_ok"] is True
    assert parsed["cam_fps"] == 5


def test_build_cam_fps_signal_pb():
    msg = build_cam_fps_signal_pb(cam_fps=5)
    assert msg["type"] == "pb_single"
    assert msg["cam_fps"] == 5


def test_pb_json_messages_cam_fps_on_chain():
    row = {"chunk_ms": 50, "anim": [make_anim_item({}, 50)]}
    pairs = pb_json_messages(
        pb_req="req1",
        sample_rate=24000,
        fmt="s16le",
        channels=1,
        anim_rows=[row],
        pcm_per_idx=[b""],
        cam_fps=parse_pb_cam_fps(4),
    )
    msg, _ = pairs[0]
    assert msg["cam_fps"] == 4
