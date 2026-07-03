"""开机后设备首次 WS 连接：下发「苏醒」表情场景。"""
from __future__ import annotations

import logging
import os
import uuid

from deskbot_server.constants import FACE_DESIGN_FILE
from deskbot_server.face_expr_scenes_store import (
    design_frames_to_pb_chain,
    find_design_scene_by_name,
    load_face_expr_scenes_file,
)
from deskbot_server.pb.shapes import PB_ACTION_REPLACE, PB_LEVEL_TASK, apply_pb_dispatch_fields
from deskbot_server.pb.servo_pcm import attach_pb_device_hints_from_config
from deskbot_server.ws.asr_chat_hub import AsrChatHub
from deskbot_server.ws.device_pipeline import publish_auto_dispatch_event

logger = logging.getLogger("deskbot-server")

BOOT_WAKE_SCENE = "wake"


async def deliver_boot_wake_scene(hub: AsrChatHub, device_id: str) -> int:
    """向设备顺序下发 deskbot-face 中的「苏醒」场景（无 PCM）。"""
    dev = str(device_id or "").strip()
    if not dev:
        return 0
    rows = load_face_expr_scenes_file(seed_if_missing=False, device_id=dev) or []
    ent = find_design_scene_by_name(rows, BOOT_WAKE_SCENE)
    if ent is None:
        logger.warning(
            "[boot_wake] 场景 %r 不在 %s 中 device_id=%s",
            BOOT_WAKE_SCENE,
            os.path.basename(FACE_DESIGN_FILE),
            dev,
        )
        return 0
    req_id = uuid.uuid4().hex[:16]
    pairs = design_frames_to_pb_chain(ent.get("frames") or [], runtime_req=req_id)
    if not pairs:
        logger.warning("[boot_wake] 场景 %r 无有效帧 device_id=%s", BOOT_WAKE_SCENE, dev)
        return 0
    frames = [msg for msg, _bins in pairs]
    binaries_per_frame = [list(_bins) for _msg, _bins in pairs]
    apply_pb_dispatch_fields(frames, action=PB_ACTION_REPLACE, level=PB_LEVEL_TASK)
    attach_pb_device_hints_from_config(frames)
    n = 0
    try:
        n = await hub.send_pb_chain_ordered(
            dev, frames, binaries_per_frame=binaries_per_frame
        )
        logger.info(
            "[boot_wake] scene=%s device_id=%s req=%s frames=%d ws_sends=%d",
            BOOT_WAKE_SCENE,
            dev,
            req_id,
            len(frames),
            n,
        )
    except Exception:
        logger.exception("[boot_wake] 下发失败 device_id=%s", dev)
    scene_title = str(ent.get("title") or BOOT_WAKE_SCENE).strip()
    await publish_auto_dispatch_event(
        hub.pipeline_broker,
        device_id=dev,
        request_id=req_id,
        source="auto_boot_wake",
        summary=f"开机苏醒 {scene_title}（{len(frames)} 帧）",
        status="ok" if n > 0 else "error",
        error=None if n > 0 else "未送达 WebSocket",
    )
    return n
