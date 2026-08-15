from __future__ import annotations

import asyncio
import logging
import time
import weakref
from typing import Any

from deskbot_server.infrastructure.llm.utils import coerce_pb_v2_downlink_payload
from deskbot_server.model.pb_seq import PbBlock, PbSeq
from deskbot_server.pb.payload_types import _is_pb_downlink_payload
from deskbot_server.pb.wire import device_pb_json_msg
from deskbot_server.utils.async_device_message_queue import AsyncDeviceMessageQueue
from deskbot_server.ws.ws_send import (
    _PerWsFireAndForget,
    _stop_pb_device_downlink_worker,
    enqueue_pb_device_downlink,
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

        # 设备消息队列：窗口流控 + 设备生命周期管理
        self._msg_queue = AsyncDeviceMessageQueue(window_size=10, idle_timeout=300)
        self._msg_queue.set_send_callback(self._do_send_to_device)
        self._queue_started = False

    def ws_asr_device_id(self, ws) -> str | None:
        return self._asr_ws_dev.get(ws)

    async def attach(self, device_id: str, ws) -> None:
        if not device_id:
            return

        # 确保队列管理器已启动
        if not self._queue_started:
            await self._msg_queue.start()
            self._queue_started = True

        stale: list[Any] = []
        async with self._lock:
            conns = self._by_device.get(device_id, set())
            stale = [old for old in conns if old is not ws]
            self._by_device.setdefault(device_id, set()).add(ws)
            self._asr_ws_dev[ws] = device_id
        ws._asr_chat_pb_serial_queue = self._device_pb_only
        for old in stale:
            await self._close_superseded_connection(device_id, old)

        # 注册设备到消息队列
        await self._msg_queue.init_device(device_id)

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
            # 设备所有连接已断开，注销消息队列
            await self._msg_queue.uninit_device(device_id)
            from deskbot_server.service.live_service import LiveService

            LiveService().stop(device_id)

    async def shutdown(self) -> None:
        """关闭消息队列管理器，清理所有设备资源。"""
        if self._queue_started:
            await self._msg_queue.stop()
            self._queue_started = False

    async def ack(self, device_id: str, ack: dict) -> None:
        """将 ACK 通知转发给消息队列的流控机制。"""
        await self._msg_queue.ack(device_id, ack)

    async def first_ws(self, device_id: str):
        """返回该 device 任意一条已连接的 ``/asr_chat`` WebSocket（供 HTTP 下行复用）。"""
        if not device_id:
            return None
        async with self._lock:
            conns = self._by_device.get(device_id, ())
            return next(iter(conns), None) if conns else None

    async def send(self, device_id: str, payload: dict | PbSeq, *, skip_idle_refresh: bool = False) -> int:
        del skip_idle_refresh  # 兼容旧调用方；idle 已由 LiveService 接管
        if not device_id:
            return 0
        if isinstance(payload, PbSeq):
            pb_seq = payload
        else:
            payload = coerce_pb_v2_downlink_payload(payload)
            if self._device_pb_only and not _is_pb_downlink_payload(payload):
                return 0
            block = PbBlock.from_wire(payload)
            pb_seq = PbSeq(req=block.req, entries=(block,))
        # 尝试经消息队列发送
        success = await self._msg_queue.send(device_id, pb_seq)
        if success:
            return 1
        # 队列中无该设备（可能未注册或已清理），检查是否有活跃连接并重新注册
        async with self._lock:
            has_conn = device_id in self._by_device and bool(self._by_device[device_id])
        if not has_conn:
            return 0
        await self._msg_queue.init_device(device_id)
        success = await self._msg_queue.send(device_id, pb_seq)
        return 1 if success else 0

    async def _do_send_to_device(self, device_id: str, block: PbBlock) -> bool:
        """消息队列回调：将单个 PbBlock 转为 wire 格式并发送到设备所有连接。"""
        t0 = time.monotonic()
        async with self._lock:
            targets = list(self._by_device.get(device_id, ()))
        if not targets:
            return False
        payload = block.to_wire()
        wire = device_pb_json_msg(payload)
        _log_pb_tx_wire(device_id, payload, wire, label="single", pcm_bytes=sum(len(b) for b in block.binaries))
        sent = 0
        for ws in targets:
            if getattr(ws, "_asr_chat_pb_serial_queue", False):
                await enqueue_pb_device_downlink(ws, wire, list(block.binaries) if block.binaries else None)
                sent += 1
            elif self._fanout.submit(ws, wire):
                sent += 1
        elapsed = (time.monotonic() - t0) * 1000
        if elapsed > 50:
            logger.warning("[_do_send] %s req=%s idx=%d type=%s send_ms=%.0f targets=%d sent=%d",
                           device_id, block.req, block.idx, block.type.wire, elapsed, len(targets), sent)
        return sent > 0
