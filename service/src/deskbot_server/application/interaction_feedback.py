"""ASR 交互反馈：收音时注视/巡查，等待 LLM 时连续点头。"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import suppress
from typing import Any, Optional

from deskbot_server.application.camera_servo_follower import (
    _GAZE_PITCH_OFFSET,
    _MAP_PITCH_SIGN,
    _MAP_YAW_SIGN,
    _SERVO_CENTER_X,
    _SERVO_CENTER_Y,
    _clamp,
    _screen_angles_from_analysis,
)
from deskbot_server.auto_reply import get_asr_voice_auto_reply_enabled
from deskbot_server.pb.llm_plan import expand_llm_moves
from deskbot_server.pb.servo_pcm import attach_pb_device_hints_from_config
from deskbot_server.pb.shapes import PB_ACTION_DEFAULT, PB_LEVEL_IDLE
from deskbot_server.ws.asr_chat_hub import AsrChatHub

logger = logging.getLogger("deskbot-server")

_LISTEN_MIN_GAP_SEC = 5.0
_FACE_STALE_SEC = 0.7
_MOTION_MS = 2000
_GAZE_SERVO_MS = 500

_listen_last_mono: dict[str, float] = {}
_face_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def note_face_analysis(device_id: str, analysis: dict[str, Any]) -> None:
    """缓存最近一帧人脸分析（供收音反馈读取）。"""
    dev = str(device_id or "").strip()
    if not dev or not isinstance(analysis, dict):
        return
    _face_cache[dev] = (time.monotonic(), dict(analysis))


def clear_face_analysis(device_id: str) -> None:
    dev = str(device_id or "").strip()
    if dev:
        _face_cache.pop(dev, None)


def get_valid_face_analysis(
    device_id: str, *, max_age_sec: float = _FACE_STALE_SEC
) -> Optional[dict[str, Any]]:
    dev = str(device_id or "").strip()
    if not dev:
        return None
    entry = _face_cache.get(dev)
    if entry is None:
        return None
    ts, analysis = entry
    if (time.monotonic() - ts) > max_age_sec:
        return None
    if not analysis.get("points"):
        return None
    screen_yaw, screen_pitch = _screen_angles_from_analysis(analysis)
    if screen_yaw is None or screen_pitch is None:
        return None
    return analysis


def _gaze_servo_step(analysis: dict[str, Any]) -> Optional[dict[str, int]]:
    screen_yaw, screen_pitch = _screen_angles_from_analysis(analysis)
    if screen_yaw is None or screen_pitch is None:
        return None
    ix = int(round(_clamp(_SERVO_CENTER_X + _MAP_YAW_SIGN * screen_yaw, 0, 180)))
    iy = int(
        round(
            _clamp(
                _SERVO_CENTER_Y + _MAP_PITCH_SIGN * screen_pitch + _GAZE_PITCH_OFFSET,
                0,
                180,
            )
        )
    )
    return {
        "xm": 0,
        "ym": 0,
        "x": ix,
        "y": iy,
        "ms": _GAZE_SERVO_MS,
    }


def listen_feedback_moves(device_id: str) -> tuple[str, list[dict[str, Any]]]:
    """返回 (kind, moves)；kind 为 ``gaze`` 或 ``patrol``。"""
    analysis = get_valid_face_analysis(device_id)
    if analysis is not None and _gaze_servo_step(analysis) is not None:
        step = _gaze_servo_step(analysis)
        assert step is not None
        return "gaze", [
            {
                "move": "__custom__",
                "ms": _MOTION_MS,
                "x": step["x"],
                "y": step["y"],
                "xm": 0,
                "ym": 0,
            }
        ]
    quarter = _MOTION_MS // 4
    return "patrol", [
        {"move": "look_left", "ms": quarter},
        {"move": "center", "ms": quarter},
        {"move": "look_right", "ms": quarter},
        {"move": "center", "ms": _MOTION_MS - 3 * quarter},
    ]


def llm_wait_nod_moves(*, device_id: Optional[str] = None) -> list[dict[str, Any]]:
    half = _MOTION_MS // 2
    return [
        {"move": "nod_head", "ms": half},
        {"move": "nod_head", "ms": _MOTION_MS - half},
    ]


def build_servo_only_pb_payload(
    moves: list[dict[str, Any]],
    *,
    device_id: str,
    request_id: Optional[str] = None,
) -> Optional[tuple[dict[str, Any], str]]:
    """LLM move 列表 → 纯舵机 ``pb_single``（无 audio/assets，避免 96KB 静音 PCM）。"""
    steps = expand_llm_moves(moves, device_id=device_id)
    if not steps:
        return None
    req_id = request_id or uuid.uuid4().hex[:16]
    chunk_ms = sum(max(1, int(s.get("ms") or 0)) for s in steps)
    payload: dict[str, Any] = {
        "type": "pb_single",
        "req": req_id,
        "idx": 0,
        "chunk_ms": chunk_ms,
        "pb_ver": 2,
        "action": PB_ACTION_DEFAULT,
        "level": PB_LEVEL_IDLE,
        "servo": [
            {
                "xm": int(s["xm"]),
                "ym": int(s["ym"]),
                "x": int(s["x"]),
                "y": int(s["y"]),
                "ms": int(s["ms"]),
            }
            for s in steps
        ],
    }
    attach_pb_device_hints_from_config(payload)
    return payload, req_id


async def _send_servo_moves(
    hub: AsrChatHub,
    device_id: str,
    moves: list[dict[str, Any]],
    *,
    source: str,
    summary: str,
) -> int:
    """下发 idle 级纯舵机 ``pb_single``，可被口播等高优先级随时打断。"""
    if not moves:
        return 0
    built = build_servo_only_pb_payload(moves, device_id=device_id)
    if built is None:
        return 0
    payload, req_id = built
    delivered = await hub.send(device_id, payload)
    logger.info(
        "[interaction_feedback] %s device_id=%s req=%s delivered=%d summary=%s "
        "servo_n=%d audio_next_bin_len=0",
        source,
        device_id,
        req_id,
        delivered,
        summary,
        len(payload.get("servo") or []),
    )
    if delivered > 0:
        from deskbot_server.ws.device_pipeline import publish_auto_dispatch_event

        await publish_auto_dispatch_event(
            hub.pipeline_broker,
            device_id=device_id,
            request_id=req_id,
            source=source,
            summary=summary,
            status="ok",
        )
    return delivered


async def maybe_send_listen_feedback(hub: AsrChatHub, device_id: str) -> None:
    """收音开始时：有效人脸则注视，否则左右巡查（2s）；同类动作间隔 ≥5s。"""
    if not get_asr_voice_auto_reply_enabled():
        return
    dev = str(device_id or "").strip()
    if not dev:
        return
    now = time.monotonic()
    last = _listen_last_mono.get(dev, 0.0)
    if now - last < _LISTEN_MIN_GAP_SEC:
        logger.debug(
            "[interaction_feedback] listen 跳过：距上次 %.1fs < %.1fs device_id=%s",
            now - last,
            _LISTEN_MIN_GAP_SEC,
            dev,
        )
        return
    if not await hub.first_ws(dev):
        return

    kind, moves = listen_feedback_moves(dev)
    summary = "收音注视人脸" if kind == "gaze" else "收音左右巡查"
    delivered = await _send_servo_moves(
        hub,
        dev,
        moves,
        source="auto_listen_feedback",
        summary=f"{summary}（{_MOTION_MS}ms）",
    )
    if delivered > 0:
        _listen_last_mono[dev] = now


async def llm_wait_nod_feedback_loop(
    hub: AsrChatHub,
    device_id: str,
    done: asyncio.Event,
) -> None:
    """ASR 有效文本进入 LLM 后：每 2s 一轮连续点头，直至 ``done``。"""
    if not get_asr_voice_auto_reply_enabled():
        return
    dev = str(device_id or "").strip()
    if not dev:
        return
    moves = llm_wait_nod_moves(device_id=dev)
    try:
        while not done.is_set():
            if await hub.first_ws(dev):
                await _send_servo_moves(
                    hub,
                    dev,
                    moves,
                    source="auto_llm_wait_nod",
                    summary=f"等待 LLM 点头（{_MOTION_MS}ms）",
                )
            try:
                await asyncio.wait_for(done.wait(), timeout=_MOTION_MS / 1000.0)
                break
            except TimeoutError:
                continue
    except asyncio.CancelledError:
        raise


def schedule_listen_feedback(hub: AsrChatHub, device_id: Optional[str]) -> None:
    dev = str(device_id or "").strip()
    if not dev:
        return
    asyncio.create_task(maybe_send_listen_feedback(hub, dev))


def start_llm_wait_nod_feedback(
    hub: AsrChatHub,
    device_id: Optional[str],
) -> tuple[asyncio.Event, asyncio.Task]:
    done = asyncio.Event()
    task = asyncio.create_task(llm_wait_nod_feedback_loop(hub, str(device_id or "").strip(), done))
    return done, task


async def stop_llm_wait_nod_feedback(
    done: asyncio.Event,
    task: asyncio.Task | None,
) -> None:
    done.set()
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
