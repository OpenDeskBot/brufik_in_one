"""LLM 调用失败时的 TTS + 连续 idle 动作兜底（缓解用户焦虑）。"""
from __future__ import annotations

import asyncio
import logging
import random
from contextlib import suppress
from typing import Any, Optional

from deskbot_server.application.interaction_feedback import _send_servo_moves
from deskbot_server.auto_reply import get_asr_voice_auto_reply_enabled

logger = logging.getLogger("deskbot-server")

_MOTION_MS = 2000
_ANIM_BG = "#101820"
_ANIM_COLOR = "#FFFFFF"

_LLM_ERROR_TTS = (
    "抱歉，我刚才走神了，你再说一遍好吗",
    "哎呀，我脑子卡住了，稍后再试试",
    "不好意思，我没听清自己想说啥，再说一次吧",
)


def llm_error_fallback_tts() -> str:
    return random.choice(_LLM_ERROR_TTS)


def llm_error_playback_moves() -> list[dict[str, Any]]:
    """口播 pb 内嵌舵机：摇头 + 低头，表达「出错了」。 """
    half = _MOTION_MS // 2
    return [
        {"move": "shake_head", "ms": half},
        {"move": "look_down", "ms": _MOTION_MS - half},
    ]


def llm_error_playback_anims() -> list[dict[str, Any]]:
    return [
        {
            "anim": "thinking",
            "ms": _MOTION_MS,
            "bg": _ANIM_BG,
            "color": _ANIM_COLOR,
        }
    ]


def llm_error_idle_moves() -> list[dict[str, Any]]:
    """等待/合成 TTS 期间循环下发的 idle 舵机（与等待 LLM 点头类似）。"""
    q = _MOTION_MS // 4
    return [
        {"move": "shake_head", "ms": q * 2},
        {"move": "look_down", "ms": q},
        {"move": "center", "ms": _MOTION_MS - 3 * q},
    ]


def build_llm_error_fallback_plan(*, tts: str | None = None) -> dict[str, Any]:
    text = (tts or llm_error_fallback_tts()).strip()
    return {
        "tts": text,
        "parsed": {
            "reply": text,
            "servo": [],
            "moves": llm_error_playback_moves(),
            "anims": llm_error_playback_anims(),
            "images": [],
            "json_ok": True,
            "need_reply": True,
            "raw": text,
        },
    }


async def llm_error_motion_loop(
    hub: Any,
    device_id: str,
    done: asyncio.Event,
) -> None:
    """LLM 失败恢复期间：每 2s 一轮 idle 舵机，直至 ``done``。"""
    if not get_asr_voice_auto_reply_enabled():
        return
    dev = str(device_id or "").strip()
    if not dev:
        return
    moves = llm_error_idle_moves()
    try:
        while not done.is_set():
            if await hub.first_ws(dev):
                await _send_servo_moves(
                    hub,
                    dev,
                    moves,
                    source="auto_llm_error_motion",
                    summary=f"LLM 失败兜底动作（{_MOTION_MS}ms）",
                )
            try:
                await asyncio.wait_for(done.wait(), timeout=_MOTION_MS / 1000.0)
                break
            except TimeoutError:
                continue
    except asyncio.CancelledError:
        raise


def start_llm_error_motion_feedback(
    hub: Any,
    device_id: Optional[str],
) -> tuple[asyncio.Event | None, asyncio.Task | None]:
    dev = str(device_id or "").strip()
    if not dev or hub is None or not get_asr_voice_auto_reply_enabled():
        return None, None
    done = asyncio.Event()
    task = asyncio.create_task(llm_error_motion_loop(hub, dev, done))
    return done, task


async def stop_llm_error_motion_feedback(
    done: asyncio.Event | None,
    task: asyncio.Task | None,
) -> None:
    if done is not None:
        done.set()
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
