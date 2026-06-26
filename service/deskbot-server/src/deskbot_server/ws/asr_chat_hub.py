from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import time
import uuid
import weakref
from typing import Any, Optional

from deskbot_server.constants import FACE_DESIGN_FILE
from deskbot_server.llm.utils import coerce_pb_v2_downlink_payload
from deskbot_server.face_expr_scenes_store import (
    design_frames_to_pb_chain,
    find_design_scene_by_name,
    load_face_expr_scenes_file,
)
from deskbot_server.pb.servo_pcm import attach_pb_device_hints_from_config
from deskbot_server.pb.shapes import (
    PB_ACTION_DEFAULT,
    PB_LEVEL_IDLE,
    apply_pb_dispatch_fields,
)
from deskbot_server.ws.pb_idle_registry import note_pb_idle_for_device
from deskbot_server.ws.device_pipeline import publish_auto_dispatch_event
from deskbot_server.settings import _is_pb_downlink_payload
from deskbot_server.pb.wire import device_pb_json_msg
from deskbot_server.util import _json_msg
from deskbot_server.ws.ws_send import (
    _pb_ws_chain_serial_lock,
    _PerWsFireAndForget,
    _safe_send,
    _stop_pb_device_downlink_worker,
    enqueue_pb_device_downlink,
    enqueue_pb_device_downlink_unlocked,
)

logger = logging.getLogger("deskbot-server")


def _log_pb_tx_wire(
    device_id: str,
    payload: dict,
    wire: str,
    *,
    label: str = "",
    pcm_bytes: int = 0,
) -> None:
    """调试：打印实际发往设备的 pb JSON 文本帧（与 ``chat_flow`` 的 wire_json 一致）。"""
    tag = f" {label}" if label else ""
    bin_note = f" +binary={pcm_bytes}" if pcm_bytes else ""
    audio_n = int((payload.get("audio") or {}).get("next_bin_len") or 0)
    logger.info(
        "[pb TX]%s device_id=%s req=%s type=%s idx=%s chunk_ms=%s "
        "anim_n=%d servo_n=%d audio_next_bin_len=%d%s wire_json %s",
        tag,
        device_id,
        payload.get("req"),
        payload.get("type"),
        payload.get("idx"),
        payload.get("chunk_ms"),
        len(payload.get("anim") or []) if isinstance(payload.get("anim"), list) else 0,
        len(payload.get("servo") or []) if isinstance(payload.get("servo"), list) else 0,
        audio_n,
        bin_note,
        wire,
    )


class AsrChatHub:
    """按 device_id 索引当前所有 /asr_chat 长连接，允许其它通道主动下发消息。

    可选用途：在 ``send_face_info_to_asr_chat`` 开启时，``/asr_chat`` 的 ``camera_frame``
    可将 ``face_info`` 写回同连接（与 ``device_pb_only`` 互斥）。

    ``device_pb_only`` 为 true 时：经 :meth:`send` 仅接受 ``pb_*`` 载荷，且与同连接 TTS 共用
    :func:`enqueue_pb_device_downlink` 队列顺序写出；其它载荷直接丢弃计数为 0。
    """

    def __init__(
        self,
        device_pb_only: bool = False,
        *,
        pipeline_broker: Optional[Any] = None,
    ) -> None:
        self._by_device: dict = {}
        self._lock = asyncio.Lock()
        # 给 ESP32 反压（比如它在播 TTS 时 RX 满）时不会卡住调用方
        self._fanout = _PerWsFireAndForget()
        # 每条 /asr_chat WebSocket -> device_id（供下行空闲打盹计时；WeakKey 随 ws 释放）
        self._asr_ws_dev = weakref.WeakKeyDictionary()
        self.pb_idle_snore: Optional[Any] = None
        self.pb_idle_silence: Optional[Any] = None
        self._device_pb_only = bool(device_pb_only)
        self.pipeline_broker = pipeline_broker
        # pb_idle_silence 自身下行：跳过一次 idle 计时刷新（异步 send 回调）
        self._skip_idle_note_once: set[str] = set()

    def consume_skip_idle_note(self, device_id: str) -> bool:
        if device_id in self._skip_idle_note_once:
            self._skip_idle_note_once.discard(device_id)
            return True
        return False

    def ws_asr_device_id(self, ws) -> Optional[str]:
        return self._asr_ws_dev.get(ws)

    async def attach(self, device_id: str, ws) -> None:
        if not device_id:
            return
        async with self._lock:
            self._by_device.setdefault(device_id, set()).add(ws)
            self._asr_ws_dev[ws] = device_id
        setattr(ws, "_asr_chat_pb_serial_queue", self._device_pb_only)
        note_pb_idle_for_device(device_id)

    async def detach(self, device_id: str, ws) -> None:
        if not device_id:
            return
        removed_last = False
        async with self._lock:
            self._asr_ws_dev.pop(ws, None)
            conns = self._by_device.get(device_id)
            if conns is None:
                return
            conns.discard(ws)
            if not conns:
                self._by_device.pop(device_id, None)
                removed_last = True
        await _stop_pb_device_downlink_worker(ws)
        self._fanout.discard(ws)
        if removed_last:
            for sched in (self.pb_idle_snore, self.pb_idle_silence):
                if sched is not None:
                    sched.cancel_for_device(device_id)

    async def first_ws(self, device_id: str):
        """返回该 device 任意一条已连接的 ``/asr_chat`` WebSocket（供 HTTP 下行复用）。"""
        if not device_id:
            return None
        async with self._lock:
            conns = self._by_device.get(device_id, ())
            return next(iter(conns), None) if conns else None

    async def send(self, device_id: str, payload: dict, *, skip_idle_refresh: bool = False) -> int:
        if not device_id:
            return 0
        payload = coerce_pb_v2_downlink_payload(payload)
        if self._device_pb_only and not _is_pb_downlink_payload(payload):
            return 0
        async with self._lock:
            targets = list(self._by_device.get(device_id, ()))
        if not targets:
            return 0
        if skip_idle_refresh:
            self._skip_idle_note_once.add(device_id)
        wire = device_pb_json_msg(payload)
        _log_pb_tx_wire(device_id, payload, wire, label="single")
        sent = 0
        for ws in targets:
            if getattr(ws, "_asr_chat_pb_serial_queue", False):
                await enqueue_pb_device_downlink(ws, wire, None)
                sent += 1
            elif self._fanout.submit(ws, wire):
                sent += 1
        if skip_idle_refresh and sent <= 0:
            self._skip_idle_note_once.discard(device_id)
        return sent

    async def send_pb_chain_ordered(
        self,
        device_id: str,
        frames: list[dict],
        *,
        pcm_per_frame: Optional[list[Optional[bytes]]] = None,
        binaries_per_frame: Optional[list[list[bytes]]] = None,
    ) -> int:
        """按顺序逐帧下发 pb JSON（经 :func:`_json_msg`），可选每帧紧随 PCM。

        ``device_pb_only`` 连接上整链持 :func:`_pb_ws_chain_serial_lock` 后经
        :func:`enqueue_pb_device_downlink_unlocked` 入队，避免协程间插队导致仅首帧到达；
        否则仍 ``await`` :func:`_safe_send` / :func:`_safe_send_pb_json_then_pcm`。
        """
        if not device_id or not frames:
            return 0
        async with self._lock:
            targets = list(self._by_device.get(device_id, ()))
        if not targets:
            return 0
        n_frames = sum(1 for f in frames if isinstance(f, dict))
        n = 0
        chain_idx = 0
        for ws in targets:
            if getattr(ws, "_asr_chat_pb_serial_queue", False):
                async with _pb_ws_chain_serial_lock(ws):
                    for i, payload in enumerate(frames):
                        if not isinstance(payload, dict):
                            continue
                        payload = coerce_pb_v2_downlink_payload(payload)
                        wire = device_pb_json_msg(payload)
                        bins: list[bytes] = []
                        if binaries_per_frame is not None and i < len(binaries_per_frame):
                            bins = list(binaries_per_frame[i] or [])
                        elif pcm_per_frame is not None and i < len(pcm_per_frame):
                            raw_pcm = pcm_per_frame[i]
                            if raw_pcm:
                                bins = [raw_pcm]
                        chain_idx += 1
                        _log_pb_tx_wire(
                            device_id,
                            payload,
                            wire,
                            label=f"chain {chain_idx}/{n_frames}",
                            pcm_bytes=sum(len(b) for b in bins),
                        )
                        await enqueue_pb_device_downlink_unlocked(ws, wire, binaries=bins)
                        n += 1
            else:
                from deskbot_server.ws.ws_send import _safe_send_pb_json_then_pcm

                for i, payload in enumerate(frames):
                    if not isinstance(payload, dict):
                        continue
                    payload = coerce_pb_v2_downlink_payload(payload)
                    wire = device_pb_json_msg(payload)
                    bins: list[bytes] = []
                    if binaries_per_frame is not None and i < len(binaries_per_frame):
                        bins = list(binaries_per_frame[i] or [])
                    elif pcm_per_frame is not None and i < len(pcm_per_frame):
                        raw_pcm = pcm_per_frame[i]
                        if raw_pcm:
                            bins = [raw_pcm]
                    chain_idx += 1
                    _log_pb_tx_wire(
                        device_id,
                        payload,
                        wire,
                        label=f"chain {chain_idx}/{n_frames}",
                        pcm_bytes=sum(len(b) for b in bins),
                    )
                    if bins:
                        from deskbot_server.ws.ws_send import _safe_send_pb_json_then_binaries

                        ok_t, ok_b = await _safe_send_pb_json_then_binaries(ws, wire, bins)
                        if not (ok_t and ok_b):
                            continue
                    else:
                        await _safe_send(ws, wire)
                    n += 1
        return n

    async def send_pb_single_then_chain_ordered(
        self,
        device_id: str,
        single_payload: dict,
        tail_frames: Optional[list[dict]],
    ) -> int:
        """在 ``device_pb_only`` 下持**同一把**链锁：先发 ``pb_single``，再顺序发 ``tail_frames``。

        用于注视/跟随舵机与 ``happy_smile`` 等场景同批入队，避免与其它下行插队。
        ``tail_frames`` 可为空，则等价于单发 ``pb_single``。
        """
        if not device_id or not isinstance(single_payload, dict):
            return 0
        single_payload = coerce_pb_v2_downlink_payload(single_payload)
        if self._device_pb_only and not _is_pb_downlink_payload(single_payload):
            return 0
        tail = [
            coerce_pb_v2_downlink_payload(f)
            for f in (tail_frames or [])
            if isinstance(f, dict)
        ]
        async with self._lock:
            targets = list(self._by_device.get(device_id, ()))
        if not targets:
            return 0
        n_tail = len(tail)
        n_total = 1 + n_tail
        n = 0
        for ws in targets:
            if getattr(ws, "_asr_chat_pb_serial_queue", False):
                async with _pb_ws_chain_serial_lock(ws):
                    wire0 = device_pb_json_msg(single_payload)
                    _log_pb_tx_wire(
                        device_id,
                        single_payload,
                        wire0,
                        label=f"single+tail 1/{n_total}",
                    )
                    await enqueue_pb_device_downlink_unlocked(ws, wire0, None)
                    n += 1
                    for ti, payload in enumerate(tail):
                        wire = device_pb_json_msg(payload)
                        _log_pb_tx_wire(
                            device_id,
                            payload,
                            wire,
                            label=f"single+tail {ti + 2}/{n_total}",
                        )
                        await enqueue_pb_device_downlink_unlocked(ws, wire, None)
                        n += 1
            else:
                wire0 = device_pb_json_msg(single_payload)
                _log_pb_tx_wire(
                    device_id,
                    single_payload,
                    wire0,
                    label=f"single+tail 1/{n_total}",
                )
                await _safe_send(ws, wire0)
                n += 1
                for ti, payload in enumerate(tail):
                    wire = device_pb_json_msg(payload)
                    _log_pb_tx_wire(
                        device_id,
                        payload,
                        wire,
                        label=f"single+tail {ti + 2}/{n_total}",
                    )
                    await _safe_send(ws, wire)
                    n += 1
        return n


class PbIdleSnoreAfterDownlink:
    """记录「距上次成功下行」的空闲时长：每次有数据写到该设备的 ``/asr_chat`` WebSocket 则重新计时；
    连续空闲 ``idle_sec`` 秒后向该设备顺序下发指定场景。多帧链在 ``device_pb_only`` 下须原子入队；
    ``level=0``（idle）+ ``action=default``：队列中更高优先级序列不超过 1 条时追加，否则丢弃（见 docs/esp32_pb_protocol.md §7）。

    与摄像头 JPEG 同步：**``is_frontal``（正脸）** 为真时刷新空闲打盹计时且**不下发**打盹场景。
    （调试页「注视感知」另含虹膜区间，仅用于舵机；打盹抑制只看正脸，避免虹膜略偏仍下发 sleep。）
    """

    _GAZE_STALE_SEC = 0.7
    _GAZE_NOTE_MIN_INTERVAL = 0.25

    def __init__(self, hub: AsrChatHub, *, idle_sec: float, scene_name: str) -> None:
        self._hub = hub
        self._idle_sec = float(idle_sec)
        self._scene_lc = (scene_name or "").strip().lower()
        self._tasks: dict = {}
        self._gaze_frontal: dict[str, bool] = {}
        self._gaze_last_mono: dict[str, float] = {}
        self._gaze_last_note_mono: dict[str, float] = {}
        # 下发 sleep_snore 等链时，各片会触发 note_activity；若此时 _reschedule 取消正在 await 的
        # _sleep_then_fire，协程会在首帧后即被取消，后续 chunk/end 发不出去。
        self._suppress_note_devices: set[str] = set()

    def _gaze_blocks_idle_snore(self, device_id: str) -> bool:
        """最近一帧摄像头仍为正脸（``is_frontal``）且流未断（无新帧超过 _GAZE_STALE_SEC 视为已离开）。"""
        if not device_id or not self._gaze_frontal.get(device_id):
            return False
        last = self._gaze_last_mono.get(device_id)
        if last is None:
            return False
        return (time.monotonic() - last) < self._GAZE_STALE_SEC

    def on_camera_gaze_tick(self, device_id: str, frontal: bool) -> None:
        """由 camera_frame 每帧调用：``frontal`` 为 ``is_frontal``；正脸时刷新打盹计时。"""
        if not device_id or self._idle_sec <= 0:
            return
        now = time.monotonic()
        self._gaze_last_mono[device_id] = now
        prev = self._gaze_frontal.get(device_id)
        self._gaze_frontal[device_id] = frontal
        if frontal:
            last_note = self._gaze_last_note_mono.get(device_id, 0.0)
            if now - last_note >= self._GAZE_NOTE_MIN_INTERVAL:
                self._gaze_last_note_mono[device_id] = now
                self.note_activity(device_id)
        elif prev is True:
            self._gaze_last_note_mono.pop(device_id, None)
            self.note_activity(device_id)

    def note_activity(self, device_id: str) -> None:
        from deskbot_server.pb_idle_dispatch import pb_idle_auto_dispatch_active

        if not pb_idle_auto_dispatch_active():
            self.cancel_for_device(device_id)
            return
        if not device_id or self._idle_sec <= 0:
            return
        if device_id in self._suppress_note_devices:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.call_soon(self._reschedule, device_id)

    def cancel_for_device(self, device_id: str) -> None:
        if not device_id:
            return
        old = self._tasks.pop(device_id, None)
        if old is not None and not old.done():
            old.cancel()
        self._gaze_frontal.pop(device_id, None)
        self._gaze_last_mono.pop(device_id, None)
        self._gaze_last_note_mono.pop(device_id, None)

    def cancel_all(self) -> None:
        for device_id in list(self._tasks.keys()):
            self.cancel_for_device(device_id)

    def _reschedule(self, device_id: str) -> None:
        old = self._tasks.pop(device_id, None)
        try:
            cur = asyncio.current_task()
        except RuntimeError:
            cur = None
        if old is not None and not old.done() and old is not cur:
            old.cancel()
        self._tasks[device_id] = asyncio.create_task(self._sleep_then_fire(device_id))

    async def _sleep_then_fire(self, device_id: str) -> None:
        this = asyncio.current_task()
        try:
            await asyncio.sleep(self._idle_sec)
            await self._deliver_scene(device_id)
        except asyncio.CancelledError:
            raise
        finally:
            if self._tasks.get(device_id) is this:
                self._tasks.pop(device_id, None)

    async def _deliver_scene(self, device_id: str) -> None:
        from deskbot_server.pb_idle_dispatch import pb_idle_auto_dispatch_active

        if not pb_idle_auto_dispatch_active():
            return
        if not self._scene_lc:
            return
        if self._gaze_blocks_idle_snore(device_id):
            logger.info(
                "[pb_idle_snore] 跳过：正脸 is_frontal，重新计时 device_id=%s scene=%s",
                device_id,
                self._scene_lc,
            )
            self.note_activity(device_id)
            return
        rows = load_face_expr_scenes_file(seed_if_missing=False, device_id=device_id) or []
        ent = find_design_scene_by_name(rows, self._scene_lc)
        if ent is None:
            logger.warning(
                "[pb_idle_snore] 场景 %r 不在 %s 中，无法下发 device_id=%s",
                self._scene_lc,
                os.path.basename(FACE_DESIGN_FILE),
                device_id,
            )
            return
        req_id = uuid.uuid4().hex[:16]
        pairs = design_frames_to_pb_chain(ent.get("frames") or [], runtime_req=req_id)
        if not pairs:
            return
        frames = [msg for msg, _bins in pairs]
        binaries_per_frame = [list(_bins) for _msg, _bins in pairs]
        apply_pb_dispatch_fields(frames, action=PB_ACTION_DEFAULT, level=PB_LEVEL_IDLE)
        self._suppress_note_devices.add(device_id)
        n = 0
        try:
            n = await self._hub.send_pb_chain_ordered(
                device_id, frames, binaries_per_frame=binaries_per_frame
            )
            logger.info(
                "[pb_idle_snore] scene=%s level=%d action=%s device_id=%s req=%s frames=%d ws_sends=%d",
                self._scene_lc,
                PB_LEVEL_IDLE,
                PB_ACTION_DEFAULT,
                device_id,
                req_id,
                len(frames),
                n,
            )
        except Exception:
            logger.exception(
                "[pb_idle_snore] 下发失败 scene=%s device_id=%s",
                self._scene_lc,
                device_id,
            )
        finally:
            self._suppress_note_devices.discard(device_id)
        scene_title = str(ent.get("title") or self._scene_lc).strip()
        await publish_auto_dispatch_event(
            self._hub.pipeline_broker,
            device_id=device_id,
            request_id=req_id,
            source="auto_idle_snore",
            summary=f"idle 场景 {scene_title}（{len(frames)} 帧）",
            status="ok" if n > 0 else "error",
            error=None if n > 0 else "未送达 WebSocket",
        )
        # 整链发完后重新起算空闲窗口（与「任一下行刷新计时」一致）
        self.note_activity(device_id)


class PbIdleSilenceServoAfterDownlink:
    """距上次成功 pb 下行空闲 ``idle_sec`` 秒后，下发低头沉默舵机（绝对 x=90 y=80）。"""

    _SILENCE_SERVO_X = 90
    _SILENCE_SERVO_Y = 80
    _SILENCE_SERVO_MS = 500

    def __init__(self, hub: AsrChatHub, *, idle_sec: float) -> None:
        self._hub = hub
        self._idle_sec = float(idle_sec)
        self._tasks: dict = {}
        # 下发低头沉默时会触发 note_activity；抑制以免取消正在 await 的计时协程
        self._suppress_note_devices: set[str] = set()
        # 已成功下发低头沉默，且其间无其它 pb 下行
        self._silence_already_sent: set[str] = set()

    def note_activity(self, device_id: str) -> None:
        from deskbot_server.pb_idle_dispatch import pb_idle_auto_dispatch_active

        if not pb_idle_auto_dispatch_active():
            self.cancel_for_device(device_id)
            return
        if not device_id or self._idle_sec <= 0:
            return
        if device_id in self._suppress_note_devices:
            return
        self._silence_already_sent.discard(device_id)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.call_soon(self._reschedule, device_id)

    def cancel_for_device(self, device_id: str) -> None:
        if not device_id:
            return
        old = self._tasks.pop(device_id, None)
        if old is not None and not old.done():
            old.cancel()
        self._silence_already_sent.discard(device_id)

    def cancel_all(self) -> None:
        for device_id in list(self._tasks.keys()):
            self.cancel_for_device(device_id)

    def _reschedule(self, device_id: str) -> None:
        old = self._tasks.pop(device_id, None)
        try:
            cur = asyncio.current_task()
        except RuntimeError:
            cur = None
        if old is not None and not old.done() and old is not cur:
            old.cancel()
        self._tasks[device_id] = asyncio.create_task(self._sleep_then_fire(device_id))

    async def _sleep_then_fire(self, device_id: str) -> None:
        this = asyncio.current_task()
        try:
            await asyncio.sleep(self._idle_sec)
            await self._deliver_silence_servo(device_id)
        except asyncio.CancelledError:
            raise
        finally:
            if self._tasks.get(device_id) is this:
                self._tasks.pop(device_id, None)

    async def _deliver_silence_servo(self, device_id: str) -> None:
        from deskbot_server.pb_idle_dispatch import pb_idle_auto_dispatch_active

        if not pb_idle_auto_dispatch_active():
            return
        if device_id in self._silence_already_sent:
            logger.info(
                "[pb_idle_silence] 跳过：上次已是低头沉默，等待其它 pb 下行 device_id=%s",
                device_id,
            )
            return
        if not await self._hub.first_ws(device_id):
            return
        req_id = uuid.uuid4().hex[:16]
        ms = self._SILENCE_SERVO_MS
        payload = {
            "type": "pb_single",
            "req": req_id,
            "idx": 0,
            "chunk_ms": ms,
            "pb_ver": 2,
            "action": PB_ACTION_DEFAULT,
            "level": PB_LEVEL_IDLE,
            "servo": [
                {
                    "xm": 0,
                    "ym": 0,
                    "x": self._SILENCE_SERVO_X,
                    "y": self._SILENCE_SERVO_Y,
                    "ms": ms,
                }
            ],
        }
        attach_pb_device_hints_from_config(payload)
        self._suppress_note_devices.add(device_id)
        n = 0
        try:
            n = await self._hub.send(device_id, payload, skip_idle_refresh=True)
            if n <= 0:
                return
            self._silence_already_sent.add(device_id)
            logger.info(
                "[pb_idle_silence] 低头沉默 device_id=%s req=%s x=%d y=%d xm=0 ym=0 delivered=%d",
                device_id,
                req_id,
                self._SILENCE_SERVO_X,
                self._SILENCE_SERVO_Y,
                n,
            )
        except Exception:
            logger.exception(
                "[pb_idle_silence] 下发失败 device_id=%s",
                device_id,
            )
            return
        finally:
            self._suppress_note_devices.discard(device_id)
        await publish_auto_dispatch_event(
            self._hub.pipeline_broker,
            device_id=device_id,
            request_id=req_id,
            source="auto_idle_silence",
            summary=(
                f"idle 低头沉默 舵机 ({self._SILENCE_SERVO_X}, {self._SILENCE_SERVO_Y})"
            ),
            status="ok",
            error=None,
        )
