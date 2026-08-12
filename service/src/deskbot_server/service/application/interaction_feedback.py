"""ASR 交互反馈：收音时注视/巡查，等待 LLM 时连续点头。"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import suppress
from typing import Any

from deskbot_server.dao.debug_prefs_store import get_camera_servo_auto_mode
from deskbot_server.dao.servo_config_store import clamp_servo_step, servo_limits
from deskbot_server.pb.llm_plan import expand_llm_moves
from deskbot_server.pb.servo_pcm import PB_CHUNK_MS_MAX, attach_pb_device_hints_from_config
from deskbot_server.pb.shapes import PB_ACTION_REPLACE, PB_LEVEL_IDLE
from deskbot_server.service.application.camera_servo_follower import (
    _GAZE_PITCH_OFFSET,
    _MAP_PITCH_SIGN,
    _MAP_YAW_SIGN,
    _SERVO_CENTER_X,
    _SERVO_CENTER_Y,
    _clamp,
    _screen_angles_from_analysis,
)
from deskbot_server.service.auto_reply import get_asr_voice_auto_reply_enabled
from deskbot_server.ws.asr_chat_hub import AsrChatHub

logger = logging.getLogger("deskbot-server")

_LISTEN_MIN_GAP_SEC = 5.0
_FACE_STALE_SEC = 0.7
_MOTION_MS = 2000
_LLM_WAIT_NOD_MS = 800
_GAZE_SERVO_MS = 500

_listen_last_mono: dict[str, float] = {}
_face_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _face_follow_active() -> bool:
    return get_camera_servo_auto_mode() in ("follow", "follow_frontal", "gaze")


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


def get_valid_face_analysis(device_id: str, *, max_age_sec: float = _FACE_STALE_SEC) -> dict[str, Any] | None:
    dev = str(device_id or "").strip()
    if not dev:
        return None
    entry = _face_cache.get(dev)
    if entry is None:
        return None
    ts, analysis = entry
    if (time.monotonic() - ts) > max_age_sec:
        return None
    if not analysis.get("landmarks"):
        return None
    screen_yaw, screen_pitch = _screen_angles_from_analysis(analysis)
    if screen_yaw is None or screen_pitch is None:
        return None
    return analysis


def _gaze_servo_step(analysis: dict[str, Any], *, device_id: str | None = None) -> dict[str, int] | None:
    screen_yaw, screen_pitch = _screen_angles_from_analysis(analysis)
    if screen_yaw is None or screen_pitch is None:
        return None
    lim = servo_limits(device_id=device_id)
    ix = int(round(_clamp(_SERVO_CENTER_X + _MAP_YAW_SIGN * screen_yaw, lim["xMin"], lim["xMax"])))
    iy = int(
        round(_clamp(_SERVO_CENTER_Y + _MAP_PITCH_SIGN * screen_pitch + _GAZE_PITCH_OFFSET, lim["yMin"], lim["yMax"]))
    )
    return clamp_servo_step({"xm": 0, "ym": 0, "x": ix, "y": iy, "ms": _GAZE_SERVO_MS}, device_id=device_id, limits=lim)


def listen_feedback_moves(device_id: str) -> tuple[str, list[dict[str, Any]]]:
    """返回 (kind, moves)；kind 为 ``gaze`` 或 ``patrol``。"""
    analysis = get_valid_face_analysis(device_id)
    if analysis is not None and _gaze_servo_step(analysis, device_id=device_id) is not None:
        step = _gaze_servo_step(analysis, device_id=device_id)
        assert step is not None
        return "gaze", [{"move": "__custom__", "ms": _MOTION_MS, "x": step["x"], "y": step["y"], "xm": 0, "ym": 0}]
    quarter = _MOTION_MS // 4
    return "patrol", [
        {"move": "look_left", "ms": quarter},
        {"move": "center", "ms": quarter},
        {"move": "look_right", "ms": quarter},
        {"move": "center", "ms": _MOTION_MS - 3 * quarter},
    ]


def llm_wait_nod_moves(*, device_id: str | None = None) -> list[dict[str, Any]]:
    """等待 LLM 时只做一次短点头，避免在 motor 队列积压完整双点头。"""
    return [{"move": "nod_head", "ms": _LLM_WAIT_NOD_MS}]


def _group_servo_steps_by_chunk_ms(
    steps: list[dict[str, Any]], *, max_chunk_ms: int = PB_CHUNK_MS_MAX
) -> list[list[dict[str, Any]]]:
    """将舵机步按 ``max_chunk_ms`` 分包；单步超时则拆成多段同姿态 hold。"""
    max_ms = max(100, int(max_chunk_ms))
    groups: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    cur_ms = 0
    for s in steps:
        ms = max(1, int(s.get("ms") or 0))
        base = {
            "xm": int(s["xm"]),
            "ym": int(s["ym"]),
            "x": int(s["x"]),
            "y": int(s["y"]),
        }
        while ms > 0:
            room = max_ms if not cur else max_ms - cur_ms
            if room <= 0:
                groups.append(cur)
                cur = []
                cur_ms = 0
                room = max_ms
            take = min(ms, room)
            cur.append({**base, "ms": take})
            cur_ms += take
            ms -= take
            if cur_ms >= max_ms:
                groups.append(cur)
                cur = []
                cur_ms = 0
    if cur:
        groups.append(cur)
    return groups


def build_servo_only_pb_frames(
    moves: list[dict[str, Any]], *, device_id: str, request_id: str | None = None
) -> tuple[list[dict[str, Any]], str] | None:
    """LLM move 列表 → 纯舵机 pb 链（每片 ``chunk_ms`` ≤ ``PB_CHUNK_MS_MAX``）。"""
    steps = expand_llm_moves(moves, device_id=device_id)
    if not steps:
        return None
    req_id = request_id or uuid.uuid4().hex[:16]
    groups = _group_servo_steps_by_chunk_ms(steps)
    frames: list[dict[str, Any]] = []
    n = len(groups)
    for i, group in enumerate(groups):
        chunk_ms = sum(max(1, int(s.get("ms") or 0)) for s in group)
        if n == 1:
            typ = "pb_single"
        elif i == 0:
            typ = "pb_start"
        elif i == n - 1:
            typ = "pb_end"
        else:
            typ = "pb_chunk"
        payload: dict[str, Any] = {
            "type": typ,
            "req": req_id,
            "idx": i,
            "chunk_ms": chunk_ms,
            "pb_ver": 2,
            # replace：同级 idle 新链清空设备 ring 积压；level=0 不抢占口播。
            # default/append 会让 live 东张西望按 chunk_ms 堆到 96 帧，anim PSRAM 易 OOM 重启。
            "action": PB_ACTION_REPLACE,
            "level": PB_LEVEL_IDLE,
            "servo": [
                {"xm": int(s["xm"]), "ym": int(s["ym"]), "x": int(s["x"]), "y": int(s["y"]), "ms": int(s["ms"])}
                for s in group
            ],
        }
        attach_pb_device_hints_from_config(payload)
        frames.append(payload)
    return frames, req_id


def build_servo_only_pb_payload(
    moves: list[dict[str, Any]], *, device_id: str, request_id: str | None = None
) -> tuple[dict[str, Any], str] | None:
    """兼容旧调用：仅当结果为单片时返回；多片请用 ``build_servo_only_pb_frames``。"""
    built = build_servo_only_pb_frames(moves, device_id=device_id, request_id=request_id)
    if built is None:
        return None
    frames, req_id = built
    if len(frames) != 1:
        # 多片时仍返回首片，避免静默丢动作；调用方应改用 frames API
        logger.warning(
            "[interaction_feedback] build_servo_only_pb_payload 得到 %d 片，仅返回首片 req=%s",
            len(frames),
            req_id,
        )
    return frames[0], req_id


async def _send_servo_moves(
    hub: AsrChatHub, device_id: str, moves: list[dict[str, Any]], *, source: str, summary: str
) -> int:
    """下发 idle 级纯舵机 pb 链，可被口播等高优先级随时打断。"""
    if not moves:
        return 0
    built = build_servo_only_pb_frames(moves, device_id=device_id)
    if built is None:
        return 0
    frames, req_id = built
    delivered = await hub.send_pb_chain_ordered(device_id, frames)
    servo_n = sum(len(f.get("servo") or []) for f in frames)
    logger.info(
        "[interaction_feedback] %s device_id=%s req=%s delivered=%d frames=%d summary=%s servo_n=%d audio_next_bin_len=0",
        source,
        device_id,
        req_id,
        delivered,
        len(frames),
        summary,
        servo_n,
    )
    if delivered > 0:
        from deskbot_server.ws.device_pipeline import publish_auto_dispatch_event

        await publish_auto_dispatch_event(
            hub.pipeline_broker, device_id=device_id, request_id=req_id, source=source, summary=summary, status="ok"
        )
    return delivered


async def maybe_send_listen_feedback(hub: AsrChatHub, device_id: str) -> None:
    """收音开始时：有效人脸则注视，否则左右巡查（2s）；同类动作间隔 ≥5s。"""
    if not get_asr_voice_auto_reply_enabled() or _face_follow_active():
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
        hub, dev, moves, source="auto_listen_feedback", summary=f"{summary}（{_MOTION_MS}ms）"
    )
    if delivered > 0:
        _listen_last_mono[dev] = now


async def llm_wait_nod_feedback_loop(hub: AsrChatHub, device_id: str, done: asyncio.Event) -> None:
    """ASR 有效文本进入 LLM 后：每 2s 一次短点头，直至 ``done``。"""
    if not get_asr_voice_auto_reply_enabled():
        return
    dev = str(device_id or "").strip()
    if not dev:
        return
    moves = llm_wait_nod_moves(device_id=dev)
    try:
        while not done.is_set():
            if not _face_follow_active() and await hub.first_ws(dev):
                await _send_servo_moves(
                    hub, dev, moves, source="auto_llm_wait_nod", summary=f"等待 LLM 点头（{_LLM_WAIT_NOD_MS}ms）"
                )
            try:
                await asyncio.wait_for(done.wait(), timeout=_MOTION_MS / 1000.0)
                break
            except TimeoutError:
                continue
    except asyncio.CancelledError:
        raise


def schedule_listen_feedback(hub: AsrChatHub, device_id: str | None) -> None:
    dev = str(device_id or "").strip()
    if not dev:
        return
    asyncio.create_task(maybe_send_listen_feedback(hub, dev))


def start_llm_wait_nod_feedback(hub: AsrChatHub, device_id: str | None) -> tuple[asyncio.Event, asyncio.Task]:
    done = asyncio.Event()
    task = asyncio.create_task(llm_wait_nod_feedback_loop(hub, str(device_id or "").strip(), done))
    return done, task


async def stop_llm_wait_nod_feedback(done: asyncio.Event, task: asyncio.Task | None) -> None:
    done.set()
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
