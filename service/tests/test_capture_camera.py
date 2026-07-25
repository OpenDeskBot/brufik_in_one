from __future__ import annotations

import asyncio

from deskbot_server.dao.device_camera_frame_store import capture_camera_for_device_async
from deskbot_server.infrastructure.llm.utils import parse_llm_reply
from deskbot_server.pb.cam_signal import build_cam_fps_signal_pb
from deskbot_server.pb.servo_pcm import make_anim_item, parse_pb_cam_fps, pb_json_messages
from deskbot_server.service.camera_face_service import CameraFaceService


def _fake_jpeg() -> bytes:
    return b"\xff\xd8\xff\xe0" + b"\x00" * 16 + b"\xff\xd9"


def test_capture_camera_for_device_async_via_video_subscribe():
    CameraFaceService.reset_instance()
    svc = CameraFaceService()
    # capture 不依赖 dp_broker；直接 _emit 模拟上行帧
    dev = "dev_async_cam"

    async def _run():
        async def _publisher():
            await asyncio.sleep(0.05)
            await svc.try_emit_video_frame(
                dev,
                _fake_jpeg(),
                meta={"frame_w": 320, "frame_h": 240, "source": "test"},
            )

        pub = asyncio.create_task(_publisher())
        cap = await capture_camera_for_device_async(dev, hub=None, wait_timeout_s=1.0)
        await pub
        return cap

    cap = asyncio.run(_run())
    assert cap["ok"] is True
    assert cap["jpeg_bytes"] > 0
    assert len(svc._video_subs) == 0


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
