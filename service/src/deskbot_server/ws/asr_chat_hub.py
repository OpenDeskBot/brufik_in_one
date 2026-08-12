from __future__ import annotations

import asyncio
import logging
import weakref
from typing import Any

from deskbot_server.infrastructure.llm.utils import coerce_pb_v2_downlink_payload
from deskbot_server.pb.payload_types import _is_pb_downlink_payload
from deskbot_server.pb.wire import device_pb_json_msg
from deskbot_server.ws.ws_send import (
    _pb_ws_chain_serial_lock,
    _PerWsFireAndForget,
    _safe_send_pb_json_then_binaries,
    _stop_pb_device_downlink_worker,
    enqueue_pb_device_downlink,
    enqueue_pb_device_downlink_unlocked,
)

logger = logging.getLogger("deskbot-server")


def _log_pb_tx_wire(device_id: str, payload: dict, wire: str, *, label: str = "", pcm_bytes: int = 0) -> None:
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

    可选用途：设备经 ``/asr_chat`` 上行 JPEG 后，服务端只做入库/
    调试预览，不再因相机结果经本 hub 回写设备。

    ``device_pb_only`` 为 true 时：经 :meth:`send` 仅接受 ``pb_*`` 载荷，且与同连接 TTS 共用
    :func:`enqueue_pb_device_downlink` 队列顺序写出；其它载荷直接丢弃计数为 0。
    """

    def __init__(self, device_pb_only: bool = False, *, pipeline_broker: Any | None = None) -> None:
        self._by_device: dict = {}
        self._lock = asyncio.Lock()
        # 给 ESP32 反压（比如它在播 TTS 时 RX 满）时不会卡住调用方
        self._fanout = _PerWsFireAndForget()
        # 每条 /asr_chat WebSocket -> device_id（WeakKey 随 ws 释放）
        self._asr_ws_dev = weakref.WeakKeyDictionary()
        self._device_pb_only = bool(device_pb_only)
        self.pipeline_broker = pipeline_broker

    def ws_asr_device_id(self, ws) -> str | None:
        return self._asr_ws_dev.get(ws)

    async def attach(self, device_id: str, ws) -> None:
        if not device_id:
            return
        stale: list[Any] = []
        async with self._lock:
            conns = self._by_device.get(device_id, set())
            stale = [old for old in conns if old is not ws]
            self._by_device.setdefault(device_id, set()).add(ws)
            self._asr_ws_dev[ws] = device_id
        setattr(ws, "_asr_chat_pb_serial_queue", self._device_pb_only)
        for old in stale:
            await self._close_superseded_connection(device_id, old)
        from deskbot_server.service.live_service import LiveService

        LiveService().start(device_id)

    async def _close_superseded_connection(self, device_id: str, ws) -> None:
        """同 device 新连接接入时关闭旧 /asr_chat，避免 delivered=2 与 zombie 连接。"""
        logger.info("[asr_chat_hub] 关闭同 device 旧 /asr_chat 连接 device_id=%s（新连接取代）", device_id)
        await self.detach(device_id, ws)
        try:
            await ws.close(code=1000, reason="superseded by new connection")
        except Exception:
            logger.debug("[asr_chat_hub] 旧连接 close 异常 device_id=%s", device_id, exc_info=True)

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
            from deskbot_server.service.live_service import LiveService

            LiveService().stop(device_id)

    async def first_ws(self, device_id: str):
        """返回该 device 任意一条已连接的 ``/asr_chat`` WebSocket（供 HTTP 下行复用）。"""
        if not device_id:
            return None
        async with self._lock:
            conns = self._by_device.get(device_id, ())
            return next(iter(conns), None) if conns else None

    async def send(self, device_id: str, payload: dict, *, skip_idle_refresh: bool = False) -> int:
        del skip_idle_refresh  # 兼容旧调用方；idle 已由 LiveService 接管
        if not device_id:
            return 0
        payload = coerce_pb_v2_downlink_payload(payload)
        if self._device_pb_only and not _is_pb_downlink_payload(payload):
            return 0
        async with self._lock:
            targets = list(self._by_device.get(device_id, ()))
        if not targets:
            return 0
        wire = device_pb_json_msg(payload)
        _log_pb_tx_wire(device_id, payload, wire, label="single")
        sent = 0
        for ws in targets:
            if getattr(ws, "_asr_chat_pb_serial_queue", False):
                await enqueue_pb_device_downlink(ws, wire, None)
                sent += 1
            elif self._fanout.submit(ws, wire):
                sent += 1
        return sent

    async def send_pb_chain_ordered(
        self,
        device_id: str,
        frames: list[dict],
        *,
        pcm_per_frame: list[bytes | None] | None = None,
        binaries_per_frame: list[list[bytes]] | None = None,
    ) -> int:
        """按顺序逐帧下发 pb JSON（经 :func:`_json_msg`），可选每帧紧随 PCM。

        ``device_pb_only`` 连接上整链持 :func:`_pb_ws_chain_serial_lock` 后经
        :func:`enqueue_pb_device_downlink_unlocked` 入队，避免协程间插队导致仅首帧到达；
        否则仍 ``await`` :meth:`WsUtils.safe_send` / :func:`_safe_send_pb_json_then_pcm`。
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
                        ok_t, ok_b = await _safe_send_pb_json_then_binaries(ws, wire, bins)
                        if not (ok_t and ok_b):
                            continue
                    else:
                        ok_t, ok_b = await _safe_send_pb_json_then_binaries(ws, wire, [])
                        if not (ok_t and ok_b):
                            continue
                    n += 1
        return n

    async def send_pb_single_then_chain_ordered(
        self, device_id: str, single_payload: dict, tail_frames: list[dict] | None
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
        tail = [coerce_pb_v2_downlink_payload(f) for f in (tail_frames or []) if isinstance(f, dict)]
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
                    _log_pb_tx_wire(device_id, single_payload, wire0, label=f"single+tail 1/{n_total}")
                    await enqueue_pb_device_downlink_unlocked(ws, wire0, None)
                    n += 1
                    for ti, payload in enumerate(tail):
                        wire = device_pb_json_msg(payload)
                        _log_pb_tx_wire(device_id, payload, wire, label=f"single+tail {ti + 2}/{n_total}")
                        await enqueue_pb_device_downlink_unlocked(ws, wire, None)
                        n += 1
            else:
                wire0 = device_pb_json_msg(single_payload)
                _log_pb_tx_wire(device_id, single_payload, wire0, label=f"single+tail 1/{n_total}")
                ok_t, ok_b = await _safe_send_pb_json_then_binaries(ws, wire0, [])
                if ok_t and ok_b:
                    n += 1
                for ti, payload in enumerate(tail):
                    wire = device_pb_json_msg(payload)
                    _log_pb_tx_wire(device_id, payload, wire, label=f"single+tail {ti + 2}/{n_total}")
                    ok_t, ok_b = await _safe_send_pb_json_then_binaries(ws, wire, [])
                    if ok_t and ok_b:
                        n += 1
        return n
