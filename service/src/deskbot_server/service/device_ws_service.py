"""设备 WebSocket 服务：连接管理 + 消息队列 + 设备注册表（单例）。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from websockets.exceptions import ConnectionClosed

from deskbot_server.constants import PB_CHUNK_GAP_SEC, PB_MAX_PCM_BIN_BYTES, SAFE_SEND_TIMEOUT
from deskbot_server.model.pb_seq import PbBlock, PbSeq, PbType
from deskbot_server.pb.wire import device_pb_json_msg
from deskbot_server.service.application.asr_chat_uplink import parse_packed_frame, pack_ws_downlink_frame
from deskbot_server.service.application.boot_wake import deliver_boot_wake_scene
from deskbot_server.service.application.chat_service import ChatService
from deskbot_server.service.camera_face_service import CameraFaceService
from deskbot_server.service.pipeline.audio import AudioConfig, ConnectionSession
from deskbot_server.service.vad_service import VadService
from deskbot_server.utils.async_helpers import spawn
from deskbot_server.utils.singleton import SingletonMeta
from deskbot_server.utils.util import _json_msg, format_exc_detail
from deskbot_server.utils.ws_utils import WsUtils
from deskbot_server.ws.uplink_rate_stats import (
    ensure_uplink_rate_stats_started,
    note_uplink_audio,
    remove_device,
)

logger = logging.getLogger("deskbot-server")


# ---------------------------------------------------------------------------
# 内部数据结构
# ---------------------------------------------------------------------------

_WINDOW_SIZE = 10
_IDLE_TIMEOUT = 300


@dataclass
class _PbDownlinkJob:
    """ws 下行 pb 发送任务。"""
    wire: str
    binaries: list[bytes] = field(default_factory=list)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    ok_json: bool = False
    ok_bins: bool = True


@dataclass
class _DeviceEntry:
    """单个设备的统一状态：元数据 + ws 连接 + 消息队列。"""

    # ── 元数据 ──
    device_id: str
    first_seen_ts: float = field(default_factory=time.time)
    last_seen_ts: float = field(default_factory=time.time)
    online: bool = True
    last_pb_ack: dict[str, Any] | None = None
    last_pb_ack_ts: float = 0.0
    last_pb_ack_mono: float = 0.0
    last_status: str | None = None
    event_count: int = 0

    # ── ws 连接（每个设备同时只有一条连接）──
    ws: Any = None  # WebSocket | None

    # ── PbSeq 消息队列（优先级 + 抢占 + ACK 流控）──
    queue: list = field(default_factory=list)                           # list[PbSeq]
    ack_queue: Any = field(default_factory=asyncio.Queue)               # asyncio.Queue[dict]
    seq_task: Any = None                                                # PbSeq 队列协程
    event: asyncio.Event = field(default_factory=asyncio.Event)
    sending_seq: Any = None                                             # PbSeq | None
    stopped: bool = False

    # ── pb downlink 队列（帧打包 + 串行发送）──
    dl_queue: Any = None                                                # asyncio.Queue[_PbDownlinkJob]
    dl_task: Any = None                                                 # downlink worker 协程


# ---------------------------------------------------------------------------
# pb wire 日志 + 帧打包
# ---------------------------------------------------------------------------


def _log_pb_tx_wire(device_id: str, payload: dict, wire: str, *, pcm_bytes: int = 0) -> None:
    audio_n = int((payload.get("audio") or {}).get("next_bin_len") or 0)
    logger.info(
        "[pb TX] device_id=%s req=%s type=%s idx=%s chunk_ms=%s "
        "anim_n=%d servo_n=%d audio_next_bin_len=%d wire_json %s",
        device_id,
        payload.get("req"),
        payload.get("type"),
        payload.get("idx"),
        payload.get("chunk_ms"),
        len(payload.get("anim") or []) if isinstance(payload.get("anim"), list) else 0,
        len(payload.get("servo") or []) if isinstance(payload.get("servo"), list) else 0,
        audio_n,
        wire,
    )


def _expected_pb_bin_lens(wire: str) -> list[int]:
    try:
        from deskbot_server.pb.servo_pcm import pb_expected_binary_lengths
        data = json.loads(wire)
        if isinstance(data, dict):
            return pb_expected_binary_lengths(data)
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return []


# ---------------------------------------------------------------------------
# DeviceWsService
# ---------------------------------------------------------------------------


class DeviceWsService(metaclass=SingletonMeta):
    """设备 WebSocket 服务（单例）。

    统一管理：设备元数据、ws 连接生命周期、PbSeq 消息队列 + 窗口流控、pb 帧下行。
    每个设备同时只有一条 WebSocket 连接。

    下行管道（两层队列串行）：
    send(PbSeq) → PbSeq 队列（优先级/抢占/ACK 流控）
      → _device_loop 取出 PbSeq → _do_send_to_device(PbBlock)
        → pb downlink 队列（帧打包/二进制校验/串行 ws.send）
    """

    def __init__(self) -> None:
        # ── 设备索引 ──
        self._devices: dict[str, _DeviceEntry] = {}
        self._lock = asyncio.Lock()

        # ── PbSeq 队列 ──
        self._queue_lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task | None = None

        # ── 应用依赖（bind 注入）──
        self._pipeline: ChatService | None = None
        self._audio_cfg: AudioConfig | None = None
        self._bus_service: Any | None = None

    @staticmethod
    def instance() -> DeviceWsService | None:
        """返回已有实例，未创建时返回 None。"""
        return SingletonMeta._instances.get(DeviceWsService)  # type: ignore[attr-defined]

    def is_device_online(self, device_id: str) -> bool:
        """判断设备是否在线。"""
        entry = self._devices.get(str(device_id or "").strip())
        return entry is not None and entry.online

    def _mark_device_online(self, device_id: str) -> None:
        """测试辅助：标记设备为在线（不建立真实 ws 连接）。"""
        did = str(device_id or "").strip()
        if not did:
            return
        if did not in self._devices:
            self._devices[did] = _DeviceEntry(device_id=did)
        self._devices[did].online = True

    # ======================================================================
    # 生命周期
    # ======================================================================

    def bind(
        self,
        pipeline: ChatService,
        audio_cfg: AudioConfig,
        bus_service: Any | None = None,
    ) -> None:
        """注入全局依赖（应用启动时调用一次）。"""
        self._pipeline = pipeline
        self._audio_cfg = audio_cfg
        self._bus_service = bus_service

    @property
    def bus_service(self):
        """暴露给应用层的 BusService 引用。"""
        return self._bus_service

    async def shutdown(self) -> None:
        """停止所有设备协程，清理资源。"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
        async with self._lock:
            for entry in list(self._devices.values()):
                await self._stop_device(entry)
        logger.info("[DeviceWsService] Shutdown complete")

    # ======================================================================
    # 连接管理（一个设备同时只有一条连接）
    # ======================================================================

    async def register(self, device_id: str, ws) -> None:
        """注册设备连接。如果已有旧连接，先清理再创建新连接。"""
        if not device_id:
            return

        if not self._cleanup_task:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

        # 如果该设备已有连接，先清理旧连接
        old_ws = None
        async with self._lock:
            old_entry = self._devices.get(device_id)
            if old_entry is not None and old_entry.ws is not None:
                old_ws = old_entry.ws
        if old_ws is not None:
            await self._close_old_connection(device_id, old_ws)

        is_new = False
        async with self._lock:
            now = time.time()
            entry = self._devices.get(device_id)
            if entry is None:
                entry = _DeviceEntry(device_id=device_id, first_seen_ts=now, last_seen_ts=now)
                self._devices[device_id] = entry
                is_new = True
            entry.last_seen_ts = now
            entry.online = True
            entry.ws = ws

        # 启动 PbSeq 队列协程
        async with self._queue_lock:
            entry.stopped = False
            if entry.seq_task is None or entry.seq_task.done():
                entry.seq_task = asyncio.create_task(self._device_loop(device_id))

        logger.info(
            "[DeviceWsService] %s device_id=%s 设备数=%d",
            "新设备" if is_new else "复用设备",
            device_id,
            len(self._devices),
        )

    async def unregister(self, device_id: str, ws) -> None:
        """注销设备连接。立即清理所有队列。"""
        if not device_id:
            return
        async with self._lock:
            entry = self._devices.get(device_id)
            if entry is None or entry.ws is not ws:
                return
            entry.ws = None
            entry.online = False
            entry.last_seen_ts = time.time()

        # 停止 downlink worker
        await self._stop_dl_worker(entry)
        # 停止 PbSeq 队列协程
        async with self._queue_lock:
            await self._stop_device(entry)

    async def _close_old_connection(self, device_id: str, old_ws) -> None:
        """关闭同设备的旧连接。"""
        logger.info("[DeviceWsService] 关闭旧连接 device_id=%s", device_id)
        await self.unregister(device_id, old_ws)
        try:
            await old_ws.close(code=1000, reason="superseded by new connection")
        except Exception:
            logger.debug("[DeviceWsService] 旧连接 close 异常 device_id=%s", device_id, exc_info=True)

    # ======================================================================
    # 连接查询
    # ======================================================================

    def _get_ws(self, device_id: str):
        """返回设备的 WebSocket 连接（无连接返回 None）。"""
        entry = self._devices.get(device_id)
        return entry.ws if entry is not None else None

    # ======================================================================
    # PbSeq 消息发送（上层队列：优先级 + 抢占 + ACK 流控）
    # ======================================================================

    async def send(
        self, device_id: str, pb_seq: PbSeq, *, wait: bool = False
    ) -> int:
        """发送 PbSeq 到设备。

        wait=True 时阻塞到设备播完（设备回 pb_ack 后解除）。
        返回 0=失败（无连接或被丢弃），1=入队成功。
        """
        if not device_id:
            return 0

        async with self._queue_lock:
            entry = self._devices.get(device_id)
            if entry is None or entry.stopped:
                logger.warning("[send] 设备不可用 device_id=%s entry=%s stopped=%s", device_id, entry is not None, getattr(entry, 'stopped', None) if entry else None)
                if wait:
                    pb_seq._done.set()
                return 0
            n = self._enqueue(entry, pb_seq)
            if n > 0:
                entry.event.set()

        if wait:
            if n > 0:
                await pb_seq._done.wait()
            else:
                pb_seq._done.set()
        return n

    async def ack(self, device_id: str, ack: dict) -> None:
        """将 ACK 通知转发给设备队列。"""
        async with self._queue_lock:
            entry = self._devices.get(device_id)
            if entry is None or entry.stopped:
                return
            await entry.ack_queue.put(ack)

    # ======================================================================
    # PbSeq 队列（优先级 + 抢占 + ACK 流控）
    # ======================================================================

    @staticmethod
    def _enqueue(entry: _DeviceEntry, new_seq: PbSeq) -> int:
        """按 level + action 规则将 PbSeq 插入队列。返回 0=丢弃，1=入队。"""
        q = entry.queue
        dev = entry.device_id
        new_info = f"req={new_seq.req} level={new_seq.level} action={new_seq.action.wire}"

        while q:
            old = q[-1]
            cmp = new_seq.compare(old)
            if cmp == 1:
                q.pop()
                logger.info("[_enqueue] %s evict queued req=%s level=%d action=%s", dev, old.req, old.level, old.action.wire)
            elif cmp == -1:
                logger.info("[_enqueue] %s drop (lower priority) %s", dev, new_info)
                return 0
            else:
                q.append(new_seq)
                logger.info("[_enqueue] %s coexist %s", dev, new_info)
                return 1

        sending = entry.sending_seq
        if sending is None:
            q.append(new_seq)
            logger.info("[_enqueue] %s enqueue (idle) %s", dev, new_info)
            return 1

        cmp = new_seq.compare(sending)
        if cmp == -1:
            logger.info("[_enqueue] %s drop (lower than running) %s", dev, new_info)
            return 0
        if cmp == 1:
            q.append(new_seq)
            entry.ack_queue.put_nowait({"type": "pb_cancel"})
            logger.info("[_enqueue] %s preempt running -> pb_cancel, %s", dev, new_info)
            return 1
        q.append(new_seq)
        logger.info("[_enqueue] %s enqueue (after running) %s", dev, new_info)
        return 1

    async def _device_loop(self, device_id: str):
        """每个设备的 PbSeq 队列协程：取 PbSeq → 逐 block 发送 → 等 ACK。"""
        entry = self._devices.get(device_id)
        if not entry:
            return
        try:
            while not entry.stopped:
                pb_seq = await self._dequeue(entry)
                if pb_seq is None:
                    break
                entry.sending_seq = pb_seq
                while not entry.ack_queue.empty():
                    try:
                        entry.ack_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                try:
                    entries = pb_seq.entries
                    n = len(entries)
                    req = pb_seq.req
                    seq_level = pb_seq.level
                    seq_sr = pb_seq.sr
                    seq_fmt = pb_seq.fmt
                    seq_ch = pb_seq.ch
                    i = 0
                    while i < n and not entry.stopped:
                        batch_end = min(i + _WINDOW_SIZE, n)
                        for bi in range(i, batch_end):
                            if not await self._do_send_to_device(
                                device_id, entries[bi],
                                level=seq_level, sr=seq_sr, fmt=seq_fmt, ch=seq_ch,
                            ):
                                return
                        ack_type = await self._wait_ack(entry, req)
                        if ack_type == "pb_cancel":
                            cancel_block = PbBlock(type=PbType.CANCEL, req=req, idx=0)
                            await self._do_send_to_device(device_id, cancel_block)
                            break
                        i = batch_end
                finally:
                    entry.sending_seq = None
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.exception("[DeviceWsService] Device loop error for %s: %s", device_id, e)
        finally:
            entry.stopped = True
            entry.sending_seq = None
            entry.event.set()

    async def _dequeue(self, entry: _DeviceEntry) -> PbSeq | None:
        while True:
            async with self._queue_lock:
                if entry.queue:
                    return entry.queue.pop(0)
                if entry.stopped:
                    return None
                entry.event.clear()
                if entry.queue:
                    return entry.queue.pop(0)
            await entry.event.wait()

    async def _wait_ack(self, entry: _DeviceEntry, req: str) -> str | None:
        while True:
            ack = await entry.ack_queue.get()
            if ack.get("type") == "pb_cancel":
                return "pb_cancel"
            if ack.get("req") != req:
                continue
            return ack.get("type")

    # ======================================================================
    # pb downlink（下层队列：帧打包 + 二进制校验 + 串行 ws.send）
    # ======================================================================

    async def _do_send_to_device(
        self, device_id: str, block: PbBlock,
        *, level: int = 1, sr: int = 0, fmt: str = "", ch: int = 0,
    ) -> bool:
        """PbBlock → wire JSON → pb downlink 队列 → ws.send。"""
        t0 = time.monotonic()
        ws = None
        async with self._lock:
            entry = self._devices.get(device_id)
            if entry is not None:
                ws = entry.ws
        if ws is None:
            return False
        payload = block.to_wire(level=level, sr=sr, fmt=fmt, ch=ch)
        wire = device_pb_json_msg(payload)
        _log_pb_tx_wire(device_id, payload, wire, pcm_bytes=sum(len(b) for b in block.binaries))
        await self._enqueue_dl(entry, wire, list(block.binaries) if block.binaries else None)
        elapsed = (time.monotonic() - t0) * 1000
        if elapsed > 50:
            logger.warning(
                "[_do_send] %s req=%s idx=%d type=%s send_ms=%.0f",
                device_id, block.req, block.idx, block.type.wire, elapsed,
            )
        return True

    async def _enqueue_dl(self, entry: _DeviceEntry, wire: str, binaries: list[bytes] | None = None, pcm: bytes | None = None):
        """将 pb 帧排入 downlink 队列，阻塞到发送完成。"""
        if entry.dl_queue is None:
            entry.dl_queue = asyncio.Queue()
        if entry.dl_task is None or entry.dl_task.done():
            entry.dl_task = asyncio.create_task(self._dl_worker(entry))
        bins = list(binaries or [])
        if pcm and (not bins or bins[0] is not pcm):
            bins = [pcm] + bins
        job = _PbDownlinkJob(wire=wire, binaries=bins)
        await entry.dl_queue.put(job)
        await job.done.wait()

    async def _dl_worker(self, entry: _DeviceEntry):
        """单设备 downlink worker：从队列取 job，打包帧 + 校验 + 串行 ws.send。"""
        q = entry.dl_queue
        while True:
            job: _PbDownlinkJob | None = await q.get()
            try:
                if job is None:
                    break
                ws = entry.ws
                if ws is None:
                    continue
                expect_lens = _expected_pb_bin_lens(job.wire)
                got_lens = [len(b) for b in job.binaries]
                if expect_lens and expect_lens != got_lens:
                    logger.error(
                        "[pb TX] binary 长度与 JSON 声明不一致 device_id=%s expect=%s got=%s",
                        entry.device_id, expect_lens, got_lens,
                    )
                if job.binaries:
                    if got_lens and got_lens[0] > PB_MAX_PCM_BIN_BYTES:
                        logger.error(
                            "[pb TX] 首包 binary %d bytes 超过 PCM 建议上限 %d device_id=%s",
                            got_lens[0], PB_MAX_PCM_BIN_BYTES, entry.device_id,
                        )
                try:
                    frame = pack_ws_downlink_frame(job.wire, job.binaries)
                except ValueError as exc:
                    logger.error("[pb TX] pack failed device_id=%s err=%s", entry.device_id, exc)
                    continue
                async with WsUtils.get_ws_send_lock(ws):
                    ok = await WsUtils.safe_send_once(
                        ws, frame, timeout=WsUtils.send_timeout_for_message(frame, base=SAFE_SEND_TIMEOUT)
                    )
                job.ok_json = ok
                job.ok_bins = ok
                if not ok:
                    logger.warning("[pb TX] 下发失败 device_id=%s", entry.device_id)
                if PB_CHUNK_GAP_SEC > 0 and job.binaries:
                    await asyncio.sleep(PB_CHUNK_GAP_SEC)
            except Exception:
                logger.exception("[pb TX] worker 异常 device_id=%s", entry.device_id)
            finally:
                if job is not None:
                    job.done.set()
                try:
                    q.task_done()
                except ValueError:
                    pass

    async def _stop_dl_worker(self, entry: _DeviceEntry):
        """停止设备的 downlink worker。"""
        if entry.dl_task is None or entry.dl_queue is None:
            return
        try:
            await entry.dl_queue.put(None)
        except Exception:
            pass
        try:
            await asyncio.wait_for(entry.dl_task, timeout=5.0)
        except (TimeoutError, asyncio.CancelledError, Exception):
            if not entry.dl_task.done():
                entry.dl_task.cancel()
                try:
                    await entry.dl_task
                except Exception:
                    pass
        entry.dl_task = None
        entry.dl_queue = None

    # ======================================================================
    # PbSeq 队列生命周期
    # ======================================================================

    async def _stop_device(self, entry: _DeviceEntry):
        """停止设备 PbSeq 队列协程。调用方须持有 self._queue_lock。"""
        entry.stopped = True
        if entry.seq_task and not entry.seq_task.done():
            entry.seq_task.cancel()
            try:
                await entry.seq_task
            except asyncio.CancelledError:
                pass
        await entry.ack_queue.put({})
        entry.event.set()

    async def _cleanup_loop(self):
        """定期清理空闲超时的设备队列协程。"""
        try:
            while True:
                await asyncio.sleep(_IDLE_TIMEOUT / 2)
                now = time.time()
                to_stop: list[_DeviceEntry] = []
                async with self._queue_lock:
                    for entry in self._devices.values():
                        if entry.seq_task and not entry.seq_task.done() and (now - entry.last_seen_ts) > _IDLE_TIMEOUT:
                            to_stop.append(entry)
                    for entry in to_stop:
                        await self._stop_device(entry)
                        logger.info("[DeviceWsService] Cleanup idle device: %s", entry.device_id)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.exception("[DeviceWsService] Cleanup error: %s", e)

    # ======================================================================
    # 设备查询
    # ======================================================================

    def snapshot(self) -> list:
        """返回所有设备的快照列表（按 last_seen 降序）。"""
        items = [self._snapshot_entry(e) for e in self._devices.values()]
        items.sort(key=lambda d: float(d.get("last_seen_ts") or 0.0), reverse=True)
        return items

    async def touch(self, device_id: str, status: str | None = None) -> None:
        """刷新设备 last_seen 时间戳。"""
        if not device_id:
            return
        async with self._lock:
            entry = self._devices.get(device_id)
            if entry is None:
                return
            entry.last_seen_ts = time.time()
            if status:
                entry.last_status = status
            entry.event_count += 1

    async def record_pb_ack(self, device_id: str, ack: dict[str, Any]) -> None:
        """记录设备最近一次 pb_ack。"""
        if not device_id or not isinstance(ack, dict):
            return
        from deskbot_server.ws.pb_ack_waiter import pb_ack_gate

        await pb_ack_gate.notify(device_id, ack)
        async with self._lock:
            entry = self._devices.get(device_id)
            if entry is None:
                logger.warning("[pb_ack] 设备未在注册表，忽略 device_id=%s", device_id)
                return
            entry.last_pb_ack = dict(ack)
            entry.last_pb_ack_ts = time.time()
            entry.last_pb_ack_mono = time.monotonic()

    async def pb_ack_llm_context(self, device_id: str | None) -> str | None:
        """返回设备最近一次 pb_ack 的 JSON（供 LLM 上下文注入）。"""
        if not device_id:
            return None
        async with self._lock:
            entry = self._devices.get(device_id)
            if not entry or not isinstance(entry.last_pb_ack, dict):
                return None
            return json.dumps(entry.last_pb_ack, ensure_ascii=False)

    @staticmethod
    def _snapshot_entry(entry: _DeviceEntry) -> dict:
        return {
            "device_id": entry.device_id,
            "first_seen_ts": entry.first_seen_ts,
            "last_seen_ts": entry.last_seen_ts,
            "online": entry.online,
            "last_status": entry.last_status,
            "event_count": entry.event_count,
            "last_pb_ack": entry.last_pb_ack,
        }

    # ======================================================================
    # WS Handler
    # ======================================================================

    async def handle_asr_chat(self, websocket, device_id: str | None) -> None:
        """/asr_chat WS：新固件打包帧上行（音频 + 摄像头）；pb_ack 流控。"""
        pipeline = self._pipeline
        audio_cfg = self._audio_cfg
        if pipeline is None or audio_cfg is None:
            raise RuntimeError("DeviceWsService 尚未 bind，请先调用 bind() 注入依赖")

        try:
            session = VadService().create_connection_session(pipeline)
        except RuntimeError:
            session = ConnectionSession(pipeline, audio_cfg)
        peer = WsUtils.peer_str(websocket)

        ensure_uplink_rate_stats_started()
        if device_id:
            await self.register(device_id, websocket)
            logger.info("[/asr_chat] 接入 device_id=%s peer=%s", device_id, peer)
        else:
            logger.warning(
                "[/asr_chat] 接入缺失 device_id peer=%s —— 不会出现在 /api/devices 设备列表，"
                "请改用 ws://host:9000/asr_chat?device_id=<设备ID>",
                peer,
            )
        try:
            ready_json = _json_msg({"type": "ready", "device_id": device_id})
            ready_ok = await WsUtils.safe_send(websocket, ready_json)
            logger.info("[/asr_chat] ready device_id=%s peer=%s sent=%s", device_id, peer, ready_ok)
            if not ready_ok:
                return
            if device_id:
                await deliver_boot_wake_scene(self, device_id)

            async for message in websocket:
                try:
                    if isinstance(message, (bytes, bytearray)):
                        payload = bytes(message)
                        frame = parse_packed_frame(payload)
                        if frame is None:
                            logger.warning("[/asr_chat] 无法解析打包帧 device_id=%s bytes=%d", device_id, len(payload))
                            continue
                        data = frame.doc
                        attached_media = frame.bin
                        msg_type = data.get("type")

                        if msg_type == "audio":
                            if not attached_media:
                                logger.warning("[/asr_chat] audio 帧缺少 binary device_id=%s", device_id)
                                continue
                            codec = data.get("codec")
                            sr_raw = data.get("sr")
                            ch_raw = data.get("ch")
                            opus_frames_raw = data.get("opus_frames") or data.get("frames") or data.get("n_frames")
                            uplink_sr = int(sr_raw) if sr_raw is not None else audio_cfg.sample_rate
                            uplink_ch = int(ch_raw) if ch_raw is not None else audio_cfg.channels
                            note_uplink_audio(device_id)
                            await self.touch(device_id)
                            opus_frames = int(opus_frames_raw) if opus_frames_raw is not None else None
                            utterance, _, _ = await session.feed_audio(
                                attached_media, codec, sample_rate=uplink_sr, channels=uplink_ch, opus_frames=opus_frames
                            )
                            if utterance:
                                logger.info("[/asr_chat] VAD切句完成 device_id=%s pcm_bytes=%d -> 触发ASR", device_id, len(utterance))
                                spawn(self._run_asr_turn(
                                    websocket, pipeline, utterance,
                                    device_id=device_id,
                                    uplink_sr=session.rom_sr,
                                    uplink_ch=session.rom_ch,
                                    uplink_codec=session.rom_codec,
                                ))
                            continue

                        if msg_type == "camera_frame":
                            if attached_media:
                                spawn(
                                    CameraFaceService().process(
                                        device_id, attached_media,
                                        frame_source="asr_chat", log_channel="/asr_chat",
                                    )
                                )
                                if self._bus_service is not None and device_id:
                                    import base64
                                    await self._bus_service.broadcast(device_id, {
                                        "type": "camera_frame",
                                        "device_id": device_id,
                                        "data": base64.b64encode(attached_media).decode("ascii"),
                                    })
                            continue

                        logger.debug("[/asr_chat] 未知打包帧 type=%r device_id=%s", msg_type, device_id)
                        continue

                    data = json.loads(message)
                    msg_type = data.get("type")

                    if msg_type == "boot_connect":
                        if device_id:
                            await deliver_boot_wake_scene(self, device_id)
                        continue

                    if msg_type == "pb_ack":
                        from deskbot_server.utils.util import _normalize_incoming_pb_ack

                        norm = _normalize_incoming_pb_ack(data)
                        if norm is not None and device_id:
                            await self.ack(device_id, norm)
                        continue
                    logger.debug("[/asr_chat] 未知消息类型 type=%r device_id=%s", msg_type, device_id)

                except Exception as exc:
                    logger.exception("处理客户端消息失败: %s", format_exc_detail(exc))
        except ConnectionClosed as closed:
            logger.info("WebSocket 已关闭: %s", closed)
        finally:
            # 调试：保存上行音频WAV
            try:
                session.save_debug_wav()
            except Exception:
                pass
            if device_id:
                remove_device(device_id)
                await self.unregister(device_id, websocket)

    async def _run_asr_turn(
        self,
        websocket,
        pipeline,
        pcm_segment: bytes,
        *,
        device_id: str | None = None,
        uplink_sr: int = 16000,
        uplink_ch: int = 1,
        uplink_codec: str = "opus",
    ) -> None:
        """VAD 切出一句后：ASR → LLM → TTS。"""
        from deskbot_server.service.application.ws_chat_turn import (
            publish_ws_chat_turn,
            run_ws_chat_turn,
        )
        from deskbot_server.service.asr_service import AsrService

        request_id = uuid.uuid4().hex[:16]
        sample_rate = uplink_sr or 16000
        seg_duration_ms = int(len(pcm_segment) / 2 / max(1, sample_rate) * 1000)
        logger.info("[ASR] 开始识别 device_id=%s req=%s pcm_bytes=%d audio_ms=%d sr=%d", device_id, request_id, len(pcm_segment), seg_duration_ms, sample_rate)
        t_asr_start = time.monotonic()

        try:
            text = await AsrService().transcribe(pcm_segment, sample_rate)
        except Exception:
            text = await pipeline.asr(pcm_segment, sample_rate=sample_rate)
        t_asr_text = time.monotonic()
        asr_ms = (t_asr_text - t_asr_start) * 1000

        if not text:
            logger.info(
                "[ASR] 结果为空 device_id=%s req=%s audio_ms=%d asr_ms=%.0f",
                device_id, request_id, seg_duration_ms, asr_ms,
            )
            return

        try:
            asr_ok = AsrService().is_valid_text(text)
        except Exception:
            asr_ok = pipeline.is_valid_asr_text(text)
        if not asr_ok:
            logger.info(
                "[ASR] 结果被过滤 device_id=%s req=%s audio_ms=%d asr_ms=%.0f text=%r",
                device_id, request_id, seg_duration_ms, asr_ms, text,
            )
            return

        logger.info(
            "[ASR] 识别成功 device_id=%s req=%s audio_ms=%d asr_ms=%.0f text=%r",
            device_id, request_id, seg_duration_ms, asr_ms, text,
        )

        try:
            flow = await run_ws_chat_turn(
                websocket,
                pipeline,
                text,
                request_id=request_id,
                device_ws=self,
                device_id=device_id,
                t_asr_start=t_asr_start,
                t_asr_text=t_asr_text,
                bus_service=self._bus_service,
            )
        except Exception as exc:
            logger.exception("[ASR] 对话轮次异常 device_id=%s req=%s", device_id, request_id)
            await publish_ws_chat_turn(
                self._bus_service,
                self,
                device_id,
                source="asr",
                asr_text=text,
                t_asr_start=t_asr_start,
                t_asr_text=t_asr_text,
                flow={"status": "error", "error": str(exc), "t_llm_end": t_asr_text, "t_tts_end": t_asr_text},
                request_id=request_id,
            )
            return
        await publish_ws_chat_turn(
            self._bus_service,
            self,
            device_id,
            source="asr",
            asr_text=text,
            t_asr_start=t_asr_start,
            t_asr_text=t_asr_text,
            flow=flow,
            request_id=request_id,
        )
