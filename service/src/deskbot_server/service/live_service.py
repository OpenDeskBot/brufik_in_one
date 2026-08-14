"""在线设备 Live 状态机：wander / sleep / gaze（PB_LEVEL_IDLE，对话期间暂停）。"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from deskbot_server.service.config_service import get_live_service_enabled
from deskbot_server.dao.face_expr_scenes_store import design_frames_to_pb_chain
from deskbot_server.pb.llm_plan import _resolve_servo_preset_steps, expand_llm_anims, expand_llm_moves
from deskbot_server.pb.shapes import PB_ACTION_APPEND, PB_LEVEL_IDLE, apply_pb_dispatch_fields
from deskbot_server.service.application.interaction_feedback import build_servo_only_pb_frames, get_valid_face_analysis
from deskbot_server.utils.singleton import SingletonMeta
from deskbot_server.ws.device_pipeline import publish_auto_dispatch_event

logger = logging.getLogger("deskbot-server")

ENTER_SEC = 5.0
FACE_STALE_SEC = 0.7
FACE_TICK_MIN_SEC = 0.22
WANDER_MIN_CYCLES = 1
WANDER_MAX_CYCLES = 3
SLEEP_MIN_SEC = 30.0
SLEEP_MAX_SEC = 60.0


class LiveState(str, Enum):
    SLEEP = "sleep"
    WANDER = "wander"
    GAZE = "gaze"


@dataclass
class _Dev:
    mode: LiveState = LiveState.WANDER
    loop: asyncio.Task | None = None
    interrupt: asyncio.Event = field(default_factory=asyncio.Event)
    in_conversation: bool = False
    resume_at: float = field(default_factory=time.monotonic)
    wander_done: int = 0
    wander_target: int = WANDER_MIN_CYCLES
    post_sleep_once: bool = False
    last_face_send: float = 0.0
    last_happy: float = 0.0


def _hold(preset: str, hold_ms: int, *, device_id: str) -> dict[str, Any]:
    steps = _resolve_servo_preset_steps(preset, device_id=device_id)
    if not steps:
        return {"move": preset, "ms": hold_ms}
    last = steps[-1]
    return {
        "move": "__custom__",
        "ms": hold_ms,
        "x": int(last.get("x", 90)),
        "y": int(last.get("y", 90)),
        "xm": 0,
        "ym": 0,
    }


def wander_moves(device_id: str) -> list[dict[str, Any]]:
    return [
        {"move": "look_left", "ms": 1000},
        _hold("look_left", 2000, device_id=device_id),
        {"move": "look_right", "ms": 2000},
        _hold("look_right", 2000, device_id=device_id),
        {"move": "center", "ms": random.randint(1000, 3000)},
    ]


def sleep_moves(device_id: str, *, hold_ms: int) -> list[dict[str, Any]]:
    hold_ms = max(1000, int(hold_ms))
    return [
        {"move": "center", "ms": 800},
        {"move": "look_down", "ms": 1000},
        _hold("look_down", hold_ms, device_id=device_id),
    ]


# 兼容旧测试名
look_around_moves = wander_moves


def _moves_ms(moves: list[dict[str, Any]], *, device_id: str) -> int:
    return sum(max(1, int(s.get("ms") or 0)) for s in expand_llm_moves(moves, device_id=device_id))


def _scene_tail(scene: str, *, device_id: str, req: str, target_ms: int) -> list[dict[str, Any]]:
    frames = expand_llm_anims([{"anim": scene, "ms": max(500, int(target_ms))}], device_id=device_id)
    if not frames:
        return []
    design = [{"ms": int(fr["ms"]), "elements": fr["elements"]} for fr in frames]
    pairs = design_frames_to_pb_chain(design, runtime_req=req)
    if not pairs:
        return []
    msgs = [msg for msg, _ in pairs]
    # 接在同轮舵机链之后；清积压由舵机链 head 的 replace 负责（见 build_servo_only_pb_frames）。
    apply_pb_dispatch_fields(msgs, action=PB_ACTION_APPEND, level=PB_LEVEL_IDLE)
    return msgs


class LiveService(metaclass=SingletonMeta):
    """每设备维护 sleep / wander / gaze；对话期间暂停，PB 使用最低 level。"""

    def __init__(self) -> None:
        self._hub: Any = None
        self._devs: dict[str, _Dev] = {}

    def bind(self, hub: Any) -> None:
        self._hub = hub

    @property
    def hub(self) -> Any:
        if self._hub is None:
            raise RuntimeError("LiveService 尚未 bind")
        return self._hub

    @staticmethod
    def active() -> bool:
        return get_live_service_enabled()

    def _ensure(self, device_id: str) -> _Dev:
        st = self._devs.get(device_id)
        if st is None:
            st = _Dev(wander_target=random.randint(WANDER_MIN_CYCLES, WANDER_MAX_CYCLES))
            self._devs[device_id] = st
        return st

    def cooldown_remaining(self, device_id: str) -> float:
        st = self._devs.get(str(device_id or "").strip())
        if st is None:
            return 0.0
        if st.in_conversation:
            return ENTER_SEC
        return max(0.0, st.resume_at - time.monotonic())

    def handles_idle(self, device_id: str) -> bool:
        if not self.active():
            return False
        st = self._devs.get(str(device_id or "").strip())
        return st is not None and st.loop is not None and not st.loop.done()

    def owns_face_tracking(self, device_id: str) -> bool:
        if not self.active():
            return False
        st = self._devs.get(str(device_id or "").strip())
        return st is not None and not st.in_conversation and self.cooldown_remaining(device_id) <= 0

    def start(self, device_id: str) -> None:
        dev = str(device_id or "").strip()
        if not dev or not self.active() or self._hub is None:
            return
        st = self._ensure(dev)
        if st.loop is None or st.loop.done():
            st.interrupt = asyncio.Event()
            st.loop = asyncio.create_task(self._loop(dev), name=f"live:{dev}")

    def stop(self, device_id: str) -> None:
        st = self._devs.pop(str(device_id or "").strip(), None)
        if st is None:
            return
        st.interrupt.set()
        if st.loop is not None and not st.loop.done():
            st.loop.cancel()

    def note_conversation_start(self, device_id: str) -> None:
        dev = str(device_id or "").strip()
        if not dev:
            return
        st = self._ensure(dev)
        st.in_conversation = True
        if st.mode == LiveState.GAZE:
            st.mode = LiveState.WANDER
        st.interrupt.set()

    def note_conversation_end(self, device_id: str) -> None:
        dev = str(device_id or "").strip()
        if not dev:
            return
        st = self._ensure(dev)
        st.in_conversation = False
        st.resume_at = time.monotonic() + ENTER_SEC
        st.mode = LiveState.WANDER
        st.interrupt.set()

    async def on_face_tick(self, device_id: str, analysis: dict[str, Any]) -> None:
        dev = str(device_id or "").strip()
        if not dev or not self.active() or not analysis.get("landmarks"):
            return
        st = self._devs.get(dev)
        if st is None or st.in_conversation or self.cooldown_remaining(dev) > 0:
            return
        if st.mode == LiveState.GAZE:
            await self._send_gaze(dev, analysis, st)
            return
        st.mode = LiveState.GAZE
        st.interrupt.set()

    def on_face_lost(self, device_id: str) -> None:
        st = self._devs.get(str(device_id or "").strip())
        if st is not None and st.mode == LiveState.GAZE:
            st.mode = LiveState.WANDER
            st.interrupt.set()

    async def _wait(self, st: _Dev, timeout: float) -> None:
        try:
            await asyncio.wait_for(st.interrupt.wait(), timeout=max(0.01, timeout))
        except TimeoutError:
            pass

    async def _loop(self, device_id: str) -> None:
        try:
            while True:
                st = self._ensure(device_id)
                st.interrupt.clear()
                if (
                    not self.active()
                    or self._hub is None
                    or not await self.hub.first_ws(device_id)
                    or st.in_conversation
                    or self.cooldown_remaining(device_id) > 0
                ):
                    await self._wait(st, 0.25)
                    continue

                if st.mode == LiveState.GAZE:
                    await self._run_gaze(device_id, st)
                    continue

                if st.mode == LiveState.SLEEP:
                    await self._run_sleep(device_id, st)
                    if not st.in_conversation and st.mode != LiveState.GAZE:
                        st.post_sleep_once = True
                        st.mode = LiveState.WANDER
                    continue

                # wander
                await self._run_wander(device_id, st)
                if st.in_conversation or st.mode == LiveState.GAZE:
                    continue
                st.wander_done += 1
                if st.post_sleep_once:
                    st.post_sleep_once = False
                    st.wander_done = 0
                    st.wander_target = random.randint(WANDER_MIN_CYCLES, WANDER_MAX_CYCLES)
                    st.resume_at = time.monotonic() + ENTER_SEC
                    st.mode = LiveState.WANDER
                elif st.wander_done >= st.wander_target:
                    st.wander_done = 0
                    st.mode = LiveState.SLEEP
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[live] loop error device_id=%s", device_id)

    async def _run_gaze(self, device_id: str, st: _Dev) -> None:
        while st.mode == LiveState.GAZE and not st.in_conversation:
            analysis = get_valid_face_analysis(device_id, max_age_sec=FACE_STALE_SEC)
            if analysis is None:
                st.mode = LiveState.WANDER
                return
            await self._send_gaze(device_id, analysis, st)
            await self._wait(st, FACE_TICK_MIN_SEC)

    async def _send_gaze(self, device_id: str, analysis: dict[str, Any], st: _Dev) -> None:
        now = time.monotonic()
        if now - st.last_face_send < FACE_TICK_MIN_SEC:
            return
        from deskbot_server.service.application.interaction_feedback import _gaze_servo_step

        step = _gaze_servo_step(analysis, device_id=device_id)
        if step is None:
            return
        moves = [{"move": "__custom__", "ms": 500, "x": step["x"], "y": step["y"], "xm": 0, "ym": 0}]
        happy = (now - st.last_happy) >= 2.0
        n = await self._send(device_id, moves, "happy" if happy else None, "auto_live_face", "live 人脸注视")
        if n > 0:
            st.last_face_send = now
            if happy:
                st.last_happy = now

    async def _run_wander(self, device_id: str, st: _Dev) -> None:
        moves = wander_moves(device_id)
        await self._timed(
            device_id,
            st,
            moves,
            "listening",
            _moves_ms(moves, device_id=device_id) / 1000.0,
            "auto_live_look_around",
            "live 东张西望",
        )

    async def _run_sleep(self, device_id: str, st: _Dev) -> None:
        dur = random.uniform(SLEEP_MIN_SEC, SLEEP_MAX_SEC)
        moves = sleep_moves(device_id, hold_ms=int(max(1000, (dur - 1.8) * 1000)))
        await self._timed(device_id, st, moves, "sleep", dur, "auto_live_sleep", f"live 睡觉（{dur:.0f}s）")

    async def _timed(
        self,
        device_id: str,
        st: _Dev,
        moves: list[dict[str, Any]],
        scene: str,
        duration_sec: float,
        source: str,
        summary: str,
    ) -> None:
        await self._send(device_id, moves, scene, source, summary)
        deadline = time.monotonic() + max(0.5, duration_sec)
        while time.monotonic() < deadline:
            if st.interrupt.is_set() or st.in_conversation or st.mode == LiveState.GAZE:
                return
            await self._wait(st, min(0.25, deadline - time.monotonic()))

    async def _send(
        self, device_id: str, moves: list[dict[str, Any]], scene: str | None, source: str, summary: str
    ) -> int:
        built = build_servo_only_pb_frames(moves, device_id=device_id)
        if built is None:
            return 0
        frames, req_id = built
        total_ms = sum(max(1, int(f.get("chunk_ms") or 0)) for f in frames)
        tail = _scene_tail(scene, device_id=device_id, req=req_id, target_ms=total_ms) if scene else []
        hub = self.hub
        try:
            if len(frames) == 1 and tail:
                n = await hub.send_pb_single_then_chain_ordered(device_id, frames[0], tail)
            elif len(frames) == 1:
                n = await hub.send(device_id, frames[0], skip_idle_refresh=True)
            else:
                n = await hub.send_pb_chain_ordered(device_id, frames)
                if tail and n > 0:
                    n += await hub.send_pb_chain_ordered(device_id, tail)
        except Exception:
            logger.exception("[live] send failed device_id=%s source=%s", device_id, source)
            return 0
        if n > 0:
            logger.info(
                "[live] %s device_id=%s req=%s delivered=%d frames=%d summary=%s scene=%s",
                source,
                device_id,
                req_id,
                n,
                len(frames),
                summary,
                scene,
            )
            await publish_auto_dispatch_event(
                hub.pipeline_broker, device_id=device_id, request_id=req_id, source=source, summary=summary, status="ok"
            )
        return n
