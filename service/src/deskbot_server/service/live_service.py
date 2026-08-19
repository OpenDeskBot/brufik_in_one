"""在线设备 Live 待机序列生成（wander / sleep / gaze，PB_LEVEL_IDLE）。"""

from __future__ import annotations

import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from deskbot_server.dao.device_mapper import get_live_mode
from deskbot_server.dao.face_expr_scenes_store import design_frames_to_pb_chain
from deskbot_server.pb.llm_plan import _resolve_servo_preset_steps, expand_llm_anims, expand_llm_moves
from deskbot_server.pb.shapes import PB_ACTION_APPEND, PB_LEVEL_IDLE, apply_pb_dispatch_fields
from deskbot_server.utils.singleton import SingletonMeta

logger = logging.getLogger("deskbot-server")

ENTER_SEC = 5.0
FACE_STALE_SEC = 0.7
FACE_TICK_MIN_SEC = 1.0
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
    last_face_send: float = 0.0
    last_happy: float = 0.0


# ---------------------------------------------------------------------------
# 舵机预设 + 序列构造辅助
# ---------------------------------------------------------------------------

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


def _scene_tail(scene: str, *, device_id: str, req: str, target_ms: int) -> list[dict[str, Any]]:
    frames = expand_llm_anims([{"anim": scene, "ms": max(500, int(target_ms))}], device_id=device_id)
    if not frames:
        return []
    design = [{"ms": int(fr["ms"]), "elements": fr["elements"]} for fr in frames]
    pairs = design_frames_to_pb_chain(design, runtime_req=req)
    if not pairs:
        return []
    msgs = [msg for msg, _ in pairs]
    apply_pb_dispatch_fields(msgs, action=PB_ACTION_APPEND, level=PB_LEVEL_IDLE)
    return msgs


# ---------------------------------------------------------------------------
# LiveService
# ---------------------------------------------------------------------------

class LiveService(metaclass=SingletonMeta):
    """生成待机 PbSeq（wander / sleep / gaze），PB 使用最低 level。"""

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
    def active(device_id: str = "") -> bool:
        if not device_id:
            return True
        return get_live_mode(device_id)

    def _ensure(self, device_id: str) -> _Dev:
        st = self._devs.get(device_id)
        if st is None:
            st = _Dev()
            self._devs[device_id] = st
        return st

    def owns_face_tracking(self, device_id: str) -> bool:
        """当前是否由 LiveService 接管人脸跟随（gaze 模式）。"""
        if not self.active(device_id):
            return False
        st = self._devs.get(str(device_id or "").strip())
        return st is not None and st.mode == LiveState.GAZE

    # ── PbSeq 构造（供 _device_loop 空闲超时调用）──

    def get_live_pb_seq(self, device_id: str) -> Any | None:
        """随机返回一个 wander 或 sleep 的 PbSeq（level=0），供设备空闲时入队。"""
        from deskbot_server.model.pb_seq import PbAction, PbBlock, PbSeq, PbServo, PbType

        if not device_id or not self.active(device_id):
            return None

        # 随机选择 wander 或 sleep
        if random.random() < 0.5:
            moves = wander_moves(device_id)
            scene = "listening"
        else:
            dur = random.uniform(SLEEP_MIN_SEC, SLEEP_MAX_SEC)
            moves = sleep_moves(device_id, hold_ms=int(max(1000, (dur - 1.8) * 1000)))
            scene = "sleep"

        steps = expand_llm_moves(moves, device_id=device_id)
        if not steps:
            return None

        req_id = uuid.uuid4().hex[:16]
        total_ms = sum(max(1, int(s.get("ms") or 0)) for s in steps)
        servo = tuple(
            PbServo(xm=int(s["xm"]), ym=int(s["ym"]), x=int(s["x"]), y=int(s["y"]), ms=int(s["ms"]))
            for s in steps
        )
        servo_block = PbBlock(type=PbType.SINGLE, req=req_id, idx=0, chunk_ms=total_ms, servo=servo)
        tail = _scene_tail(scene, device_id=device_id, req=req_id, target_ms=total_ms)
        tail_entries = tuple(PbBlock.from_wire(f) for f in tail)
        entries = (servo_block,) + tail_entries
        pb_seq = PbSeq(req=req_id, entries=entries, level=0, action=PbAction.DEFAULT)
        logger.info(
            "[live] get_live_pb_seq device_id=%s req=%s blocks=%d scene=%s ms=%d",
            device_id, req_id, len(entries), scene, total_ms,
        )
        return pb_seq

    # ── gaze（由 camera_face_service 驱动）──

    async def on_face_tick(self, device_id: str, analysis: dict[str, Any]) -> None:
        dev = str(device_id or "").strip()
        if not dev or not self.active(dev) or not analysis.get("landmarks"):
            return
        st = self._ensure(dev)
        if st.mode == LiveState.GAZE:
            await self._send_gaze(dev, analysis, st)
            return
        st.mode = LiveState.GAZE

    def on_face_lost(self, device_id: str) -> None:
        st = self._devs.get(str(device_id or "").strip())
        if st is not None and st.mode == LiveState.GAZE:
            st.mode = LiveState.WANDER

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

    async def _send(
        self, device_id: str, moves: list[dict[str, Any]], scene: str | None, source: str, summary: str
    ) -> int:
        """构造并发送 PbSeq（gaze 专用）。"""
        from deskbot_server.model.pb_seq import PbAction, PbBlock, PbSeq, PbServo, PbType

        steps = expand_llm_moves(moves, device_id=device_id)
        if not steps:
            return 0
        req_id = uuid.uuid4().hex[:16]
        total_ms = sum(max(1, int(s.get("ms") or 0)) for s in steps)
        servo = tuple(
            PbServo(xm=int(s["xm"]), ym=int(s["ym"]), x=int(s["x"]), y=int(s["y"]), ms=int(s["ms"]))
            for s in steps
        )
        servo_block = PbBlock(type=PbType.SINGLE, req=req_id, idx=0, chunk_ms=total_ms, servo=servo)
        tail = _scene_tail(scene, device_id=device_id, req=req_id, target_ms=total_ms) if scene else []
        hub = self.hub
        try:
            tail_entries = tuple(PbBlock.from_wire(f) for f in tail)
            entries = (servo_block,) + tail_entries
            pb_seq = PbSeq(req=req_id, entries=entries, level=0, action=PbAction.DEFAULT)
            n = await hub.send(device_id, pb_seq)
        except Exception:
            logger.exception("[live] send failed device_id=%s source=%s", device_id, source)
            return 0
        if n > 0:
            logger.info(
                "[live] %s device_id=%s req=%s delivered=%d blocks=%d servo_steps=%d ms=%d summary=%s scene=%s",
                source,
                device_id,
                req_id,
                n,
                len(entries),
                len(steps),
                total_ms,
                summary,
                scene,
            )
        return n
