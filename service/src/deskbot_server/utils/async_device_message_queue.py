"""Per-device PbSeq 消息队列，带优先级插入、抢占取消和 ACK 流控。

每个设备拥有独立协程，从队列中取 PbSeq 并逐 block 发送。
发送窗口：每 ``window_size`` 个 block 后暂停，等待 ACK 再继续。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from deskbot_server.model.pb_seq import PbBlock, PbSeq, PbType

logger = logging.getLogger("deskbot-server")


# ---------------------------------------------------------------------------
# 内部数据结构
# ---------------------------------------------------------------------------


@dataclass
class DeviceContext:
    """单个设备的队列上下文。"""

    device_id: str
    queue: list[PbSeq] = field(default_factory=list)
    ack_queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    last_activity: float = field(default_factory=time.time)
    task: Optional[asyncio.Task] = None
    event: asyncio.Event = field(default_factory=asyncio.Event)
    sending_seq: Optional[PbSeq] = None
    stopped: bool = False


# ---------------------------------------------------------------------------
# 队列管理器
# ---------------------------------------------------------------------------

_ACK_TIMEOUT: float = 8.0


class AsyncDeviceMessageQueue:
    """Per-device PbSeq 消息队列。

    - ``send(device_id, PbSeq)``：非阻塞入队，按 level + action 优先级插入。
    - ``ack(device_id, ack_dict)``：外部通知 ACK 到达，放入设备 ack_queue。
    - 每个设备独立协程：逐 block 发送，每 ``window_size`` 个 block 等待 ACK。
    - 高优先级 PbSeq 可抢占正在发送的序列（发 PbCancel 后切换）。
    """

    def __init__(
        self,
        window_size: int = 10,
        idle_timeout: int = 300,
    ):
        self.window_size = window_size
        self.idle_timeout = idle_timeout
        self.devices: dict[str, DeviceContext] = {}
        self._lock = asyncio.Lock()
        self._send_callback: Optional[Callable[[str, PbBlock], Awaitable[bool]]] = None
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False

    # -- 配置 / 生命周期 -------------------------------------------------------

    def set_send_callback(self, callback: Callable[[str, PbBlock], Awaitable[bool]]):
        """设置实际发送单个 PbBlock 的回调。"""
        self._send_callback = callback

    async def start(self):
        if self._running:
            return
        self._running = True
        if self.idle_timeout > 0:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("[AsyncDeviceMessageQueue] Started")

    async def stop(self):
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        async with self._lock:
            for device_id, ctx in list(self.devices.items()):
                await self._stop_device(ctx)
            self.devices.clear()
        logger.info("[AsyncDeviceMessageQueue] Stopped")

    # -- 设备管理 --------------------------------------------------------------

    async def init_device(self, device_id: str) -> bool:
        async with self._lock:
            if device_id in self.devices:
                ctx = self.devices[device_id]
                ctx.last_activity = time.time()
                if ctx.stopped:
                    ctx.stopped = False
                    ctx.task = asyncio.create_task(self._device_loop(device_id))
                return True
            ctx = DeviceContext(device_id=device_id)
            ctx.task = asyncio.create_task(self._device_loop(device_id))
            self.devices[device_id] = ctx
            logger.info("[AsyncDeviceMessageQueue] Init device: %s", device_id)
            return True

    async def uninit_device(self, device_id: str):
        async with self._lock:
            if device_id in self.devices:
                ctx = self.devices[device_id]
                await self._stop_device(ctx)
                del self.devices[device_id]
                logger.info("[AsyncDeviceMessageQueue] Uninit device: %s", device_id)

    async def _stop_device(self, ctx: DeviceContext):
        ctx.stopped = True
        if ctx.task and not ctx.task.done():
            ctx.task.cancel()
            try:
                await ctx.task
            except asyncio.CancelledError:
                pass
        # 放入哨兵唤醒阻塞的 _wait_ack
        await ctx.ack_queue.put({})
        ctx.event.set()

    # -- 公共 API --------------------------------------------------------------

    async def send(self, device_id: str, pb_seq: PbSeq) -> bool:
        """将 PbSeq 入队（非阻塞）。设备协程会在后台逐 block 发送。"""
        async with self._lock:
            ctx = self.devices.get(device_id)
            if ctx is None or ctx.stopped:
                return False
            n = self._enqueue(ctx, pb_seq)
            if n > 0:
                ctx.last_activity = time.time()
                ctx.event.set()
        return True

    async def ack(self, device_id: str, ack: dict) -> None:
        """外部 ACK 到达通知。将 ack 放入设备的 ack_queue。"""
        ctx = self.devices.get(device_id)
        if ctx is None or ctx.stopped:
            return
        await ctx.ack_queue.put(ack)

    # -- 优先级入队 ------------------------------------------------------------

    @staticmethod
    def _enqueue(ctx: DeviceContext, new_seq: PbSeq) -> int:
        """按 level + action 规则将 PbSeq 插入队列。

        Returns:
            0 表示丢弃（优先级低），1 表示入队成功。
        """
        q = ctx.queue
        dev = ctx.device_id
        new_info = f"req={new_seq.req} level={new_seq.level} action={new_seq.action.wire}"

        # 1. 与队列中的序列逐一比较（从队尾开始）
        while q:
            old = q[-1]
            cmp = new_seq.compare(old)
            if cmp == 1:
                q.pop()  # new_seq 优先级更高，踢掉队尾
                logger.info("[_enqueue] %s evict queued req=%s level=%d action=%s", dev, old.req, old.level, old.action.wire)
            elif cmp == -1:
                logger.info("[_enqueue] %s drop (lower priority) %s", dev, new_info)
                return 0  # new_seq 优先级更低，丢弃
            else:
                # cmp == 0，并存，追加到队尾
                q.append(new_seq)
                logger.info("[_enqueue] %s coexist %s", dev, new_info)
                return 1

        # 2. 队列已空，与正在发送的 PbSeq 比较
        sending = ctx.sending_seq
        if sending is None:
            q.append(new_seq)
            logger.info("[_enqueue] %s enqueue (idle) %s", dev, new_info)
            return 1

        cmp = new_seq.compare(sending)
        if cmp == -1:
            logger.info("[_enqueue] %s drop (lower than running req=%s level=%d action=%s) %s",
                        dev, sending.req, sending.level, sending.action.wire, new_info)
            return 0  # 丢弃
        if cmp == 1:
            q.append(new_seq)
            ctx.ack_queue.put_nowait({"type": "pb_cancel"})
            logger.info("[_enqueue] %s preempt running req=%s level=%d action=%s -> pb_cancel, %s",
                        dev, sending.req, sending.level, sending.action.wire, new_info)
            return 1
        # cmp == 0，入队等待
        q.append(new_seq)
        logger.info("[_enqueue] %s enqueue (after running) %s", dev, new_info)
        return 1

    # -- 设备协程 --------------------------------------------------------------

    async def _device_loop(self, device_id: str):
        """每个设备的独立协程：从队列取 PbSeq，逐 block 发送 + ACK 流控。"""
        ctx = self.devices.get(device_id)
        if not ctx:
            return

        try:
            while self._running and not ctx.stopped:
                t0 = time.monotonic()
                pb_seq = await self._dequeue(ctx)
                if pb_seq is None:
                    break
                t_dequeue = time.monotonic()

                ctx.sending_seq = pb_seq
                # 排空残留 ACK
                drained = 0
                while not ctx.ack_queue.empty():
                    try:
                        ctx.ack_queue.get_nowait()
                        drained += 1
                    except asyncio.QueueEmpty:
                        break
                if drained:
                    logger.info("[_device_loop] %s drained %d stale acks", device_id, drained)
                logger.info("[_device_loop] %s start req=%s level=%d action=%s blocks=%d wait_ms=%.0f",
                            device_id, pb_seq.req, pb_seq.level, pb_seq.action.wire,
                            len(pb_seq.entries), (t_dequeue - t0) * 1000)
                try:
                    entries = pb_seq.entries
                    n = len(entries)
                    req = pb_seq.req
                    i = 0
                    while i < n and not ctx.stopped and self._running:
                        batch_end = min(i + self.window_size, n)
                        is_last = batch_end >= n

                        # 发送本批次
                        t_batch = time.monotonic()
                        for bi in range(i, batch_end):
                            block_entry = entries[bi]
                            if not await self._internal_send(device_id, ctx, block_entry):
                                return
                        t_sent = time.monotonic()
                        # 等待 ACK：从 ack_queue 中读取，匹配 req + ack_type
                        ack_type = await self._wait_ack(ctx, req)
                        t_acked = time.monotonic()
                        logger.info("[_device_loop] %s batch[%d:%d] send_ms=%.0f ack_ms=%.0f ack_type=%s",
                                    device_id, i, batch_end,
                                    (t_sent - t_batch) * 1000, (t_acked - t_sent) * 1000, ack_type)
                        if ack_type == "pb_cancel":
                            await self._send_cancel(device_id, ctx, pb_seq)
                            break
                        i = batch_end
                finally:
                    ctx.sending_seq = None
                    logger.info("[_device_loop] %s done req=%s total_ms=%.0f",
                                device_id, pb_seq.req, (time.monotonic() - t0) * 1000)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.exception("[AsyncDeviceMessageQueue] Device loop error for %s: %s", device_id, e)
        finally:
            ctx.stopped = True
            ctx.sending_seq = None
            ctx.event.set()

    async def _dequeue(self, ctx: DeviceContext) -> PbSeq | None:
        """阻塞等待直到队列中有 PbSeq 或设备停止。"""
        while True:
            if ctx.queue:
                return ctx.queue.pop(0)
            if ctx.stopped or not self._running:
                return None
            ctx.event.clear()
            if ctx.queue:  # clear 后再检查一次，避免 lost wakeup
                return ctx.queue.pop(0)
            await ctx.event.wait()

    async def _send_cancel(self, device_id: str, ctx: DeviceContext, old_seq: PbSeq):
        """发送 PbCancel block 通知设备终止旧序列。"""
        cancel_block = PbBlock(type=PbType.CANCEL, req=old_seq.req, idx=0)
        await self._internal_send(device_id, ctx, cancel_block)
    
    async def _internal_send(self, device_id: str, ctx: DeviceContext, block_entry: PbBlock) -> bool:
        """内部发送单个 PbBlock，返回是否成功。"""
        if not self._send_callback:
            logger.error("[AsyncDeviceMessageQueue] No send callback configured")
            return False
        try:
            return await self._send_callback(device_id, block_entry)
        except Exception as e:
            logger.error("[AsyncDeviceMessageQueue] Send error device=%s req=%s idx=%d: %s", device_id, block_entry.req, block_entry.idx, e)
            return False

    async def _wait_ack(self, ctx: DeviceContext, req: str) -> str | None:
        """从 ack_queue 中等待匹配 req 的 ACK。

        非匹配的 ACK 和 pb_cancel 以外的哨兵消息被丢弃。
        收到 pb_cancel 时返回 "pb_cancel"。
        """
        while True:
            ack = await ctx.ack_queue.get()
            if ack.get("type") == "pb_cancel":
                return "pb_cancel"
            if ack.get("req") != req:
                continue
            return ack.get("type")

    # -- 空闲清理 --------------------------------------------------------------

    async def _cleanup_loop(self):
        try:
            while self._running:
                await asyncio.sleep(self.idle_timeout / 2)
                if not self._running:
                    break
                now = time.time()
                to_remove: list[tuple[str, DeviceContext]] = []
                async with self._lock:
                    for device_id, ctx in self.devices.items():
                        if (now - ctx.last_activity) > self.idle_timeout:
                            to_remove.append((device_id, ctx))
                    for device_id, ctx in to_remove:
                        await self._stop_device(ctx)
                        del self.devices[device_id]
                        logger.info("[AsyncDeviceMessageQueue] Cleanup device: %s", device_id)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.exception("[AsyncDeviceMessageQueue] Cleanup error: %s", e)
