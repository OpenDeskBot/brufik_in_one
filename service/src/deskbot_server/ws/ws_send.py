from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Optional

from deskbot_server.constants import PB_CHUNK_GAP_SEC, PB_MAX_PCM_BIN_BYTES, SAFE_SEND_TIMEOUT
from deskbot_server.service.application.asr_chat_uplink import pack_ws_downlink_frame
from deskbot_server.utils.ws_utils import WsUtils
from deskbot_server.ws.pb_idle_registry import note_pb_idle_after_successful_asr_send

logger = logging.getLogger("deskbot-server")

# 兼容旧调用名（实现已迁入 WsUtils）
_peer_str = WsUtils.peer_str
_safe_send = WsUtils.safe_send
_safe_send_once = WsUtils.safe_send_once
_get_ws_send_lock = WsUtils.get_ws_send_lock
_send_timeout_for_message = WsUtils.send_timeout_for_message


class _PerWsFireAndForget:
    """每个 ws 同时最多保留 1 个未完成的发送任务；发送未完成时消息进入待发送队列。

    用于把"广播给若干订阅者"从同步 ``await ws.send`` 改成非阻塞调度：
    - 任一订阅者写得慢/挂死，绝不会反压回到调用方协程
    - 慢订阅者代价是降帧（直到队列发完或超时关闭），但**生产端永远不卡**
    - 待发送队列有上限，避免慢订阅者无限堆积
    - 配合 :meth:`WsUtils.safe_send` 内置 ``timeout`` 保底——单个 inflight 任务最坏
      ``WS_SEND_TIMEOUT_SEC`` 秒后必然结束（超时则主动 close 该 ws，
      下一次 publish 直接 done）。
    """

    _MAX_PENDING = 32

    def __init__(self) -> None:
        self._inflight: dict = {}
        self._pending: dict = {}

    async def _drain(self, ws, message) -> None:
        while message is not None:
            await WsUtils.safe_send(ws, message)
            q = self._pending.get(ws)
            if q:
                try:
                    message = q.popleft()
                except IndexError:
                    message = None
                    self._pending.pop(ws, None)
            else:
                message = None

    def submit(self, ws, message) -> bool:
        """非阻塞地往 ``ws`` 发一条消息。返回是否真正提交（False = 被丢弃）。"""
        prev = self._inflight.get(ws)
        if prev is not None and not prev.done():
            q = self._pending.get(ws)
            if q is None:
                from collections import deque

                q = deque(maxlen=self._MAX_PENDING)
                self._pending[ws] = q
            q.append(message)
            return True
        self._inflight[ws] = asyncio.create_task(self._drain(ws, message))
        return True

    def discard(self, ws) -> None:
        """清理某 ws 的 inflight task（订阅者断开时调用）。"""
        task = self._inflight.pop(ws, None)
        self._pending.pop(ws, None)
        if task is not None and not task.done():
            task.cancel()


async def _safe_send_pb_json_then_pcm(
    websocket, text_msg: str, pcm: bytes, *, timeout: float = SAFE_SEND_TIMEOUT
) -> tuple[bool, bool]:
    """打包为单帧 BIN 下发（json + 可选 PCM）。返回 ``(ok, ok)``。"""
    return await _safe_send_pb_json_then_binaries(
        websocket, text_msg, [pcm] if pcm else [], timeout=timeout
    )


_PB_DEVICE_QUEUE_ATTR = "_bot_pb_device_downlink_queue"
_PB_DEVICE_WORKER_ATTR = "_bot_pb_device_downlink_worker"
_PB_WS_CHAIN_SERIAL_LOCK_ATTR = "_bot_pb_ws_chain_serial_lock"


def _pb_ws_chain_serial_lock(ws) -> asyncio.Lock:
    """``device_pb_only`` 连接上：保证整段 pb 链（TTS 一轮、``send_pb_chain_ordered``）在入队时不被插队。"""
    lock = getattr(ws, _PB_WS_CHAIN_SERIAL_LOCK_ATTR, None)
    if lock is None:
        lock = asyncio.Lock()
        setattr(ws, _PB_WS_CHAIN_SERIAL_LOCK_ATTR, lock)
    return lock


@asynccontextmanager
async def _maybe_pb_serial_chain_guard(ws):
    """仅 ``_asr_chat_pb_serial_queue`` 为真时持链锁；否则空上下文。"""
    if getattr(ws, "_asr_chat_pb_serial_queue", False):
        async with _pb_ws_chain_serial_lock(ws):
            yield
    else:
        yield


@dataclass
class _PbDeviceJob:
    wire: str
    binaries: list[bytes] = field(default_factory=list)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    ok_json: bool = False
    ok_bins: bool = True


def _expected_pb_bin_lens(wire: str) -> list[int]:
    try:
        import json

        from deskbot_server.pb.servo_pcm import pb_expected_binary_lengths

        data = json.loads(wire)
        if isinstance(data, dict):
            return pb_expected_binary_lengths(data)
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return []


def _expected_audio_bin_len(wire: str) -> int:
    lens = _expected_pb_bin_lens(wire)
    return lens[0] if lens else 0


async def _safe_send_pb_json_then_binaries(
    websocket, text_msg: str, binaries: list[bytes], *, timeout: float = SAFE_SEND_TIMEOUT
) -> tuple[bool, bool]:
    """单帧 BIN：``u32be(json_len)+json+concat(binaries)``（与上行对称）。"""
    try:
        frame = pack_ws_downlink_frame(text_msg, binaries)
    except ValueError as exc:
        logger.error("[pb TX] pack failed peer=%s err=%s", _peer_str(websocket), exc)
        return False, False
    async with WsUtils.get_ws_send_lock(websocket):
        ok = await WsUtils.safe_send_once(
            websocket, frame, timeout=WsUtils.send_timeout_for_message(frame, base=timeout)
        )
        if ok:
            note_pb_idle_after_successful_asr_send(websocket)
        return ok, ok


async def _pb_device_downlink_worker(ws) -> None:
    """单连接一条队列：顺序执行 pb JSON + 紧随 binary 链。"""
    q: asyncio.Queue = getattr(ws, _PB_DEVICE_QUEUE_ATTR)
    while True:
        job = await q.get()
        try:
            if job is None:
                break
            expect_lens = _expected_pb_bin_lens(job.wire)
            got_lens = [len(b) for b in job.binaries]
            if expect_lens and expect_lens != got_lens:
                logger.error(
                    "[pb TX] binary 长度与 JSON 声明不一致 peer=%s expect=%s got=%s",
                    _peer_str(ws),
                    expect_lens,
                    got_lens,
                )
            if job.binaries:
                if got_lens and got_lens[0] > PB_MAX_PCM_BIN_BYTES:
                    logger.error(
                        "[pb TX] 首包 binary %d bytes 超过 PCM 建议上限 %d peer=%s",
                        got_lens[0],
                        PB_MAX_PCM_BIN_BYTES,
                        _peer_str(ws),
                    )
                ok_t, ok_b = await _safe_send_pb_json_then_binaries(ws, job.wire, job.binaries)
                job.ok_json, job.ok_bins = ok_t, ok_b
                if not ok_t or not ok_b:
                    logger.warning(
                        "[pb TX] 下发失败 peer=%s json_ok=%s bins_ok=%s expect=%s got=%s",
                        _peer_str(ws),
                        ok_t,
                        ok_b,
                        expect_lens,
                        got_lens,
                    )
            else:
                if expect_lens:
                    logger.warning(
                        "[pb TX] JSON 声明 %d 个 binary 但 payload 为空 peer=%s", len(expect_lens), _peer_str(ws)
                    )
                ok_t, ok_b = await _safe_send_pb_json_then_binaries(ws, job.wire, [])
                job.ok_json, job.ok_bins = ok_t, ok_b
                if not job.ok_json:
                    logger.warning("[pb TX] JSON 下发失败 peer=%s", _peer_str(ws))
            if PB_CHUNK_GAP_SEC > 0 and job.binaries:
                await asyncio.sleep(PB_CHUNK_GAP_SEC)
        except Exception:
            logger.exception("[pb TX] worker 异常 peer=%s", _peer_str(ws))
        finally:
            if job is not None:
                job.done.set()
            try:
                q.task_done()
            except ValueError:
                pass


def _ensure_pb_device_downlink_worker(ws) -> None:
    if getattr(ws, _PB_DEVICE_WORKER_ATTR, None) is not None:
        return
    q: asyncio.Queue = asyncio.Queue()
    setattr(ws, _PB_DEVICE_QUEUE_ATTR, q)
    setattr(ws, _PB_DEVICE_WORKER_ATTR, asyncio.create_task(_pb_device_downlink_worker(ws)))


async def enqueue_pb_device_downlink_unlocked(
    ws, wire: str, binaries: Optional[list[bytes]] = None, pcm: Optional[bytes] = None
) -> None:
    """将 pb 下行排入队列（不设链锁；链式发送方须已持 :func:`_pb_ws_chain_serial_lock`）。"""
    _ensure_pb_device_downlink_worker(ws)
    q: asyncio.Queue = getattr(ws, _PB_DEVICE_QUEUE_ATTR)
    bins = list(binaries or [])
    if pcm and (not bins or bins[0] is not pcm):
        bins = [pcm] + bins
    job = _PbDeviceJob(wire=wire, binaries=bins)
    await q.put(job)
    await job.done.wait()


async def enqueue_pb_device_downlink(
    ws, wire: str, binaries: Optional[list[bytes]] = None, pcm: Optional[bytes] = None
) -> None:
    """单条 pb 入队；``device_pb_only`` 时持链锁，避免与其它生产者单片交叉。"""
    if getattr(ws, "_asr_chat_pb_serial_queue", False):
        async with _pb_ws_chain_serial_lock(ws):
            await enqueue_pb_device_downlink_unlocked(ws, wire, binaries=binaries, pcm=pcm)
    else:
        await enqueue_pb_device_downlink_unlocked(ws, wire, binaries=binaries, pcm=pcm)


async def _stop_pb_device_downlink_worker(ws) -> None:
    task = getattr(ws, _PB_DEVICE_WORKER_ATTR, None)
    q = getattr(ws, _PB_DEVICE_QUEUE_ATTR, None)
    if task is None or q is None:
        return
    try:
        await q.put(None)
    except Exception:
        pass
    try:
        await asyncio.wait_for(task, timeout=5.0)
    except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
        if not task.done():
            task.cancel()
            try:
                await task
            except Exception:
                pass
    try:
        delattr(ws, _PB_DEVICE_WORKER_ATTR)
        delattr(ws, _PB_DEVICE_QUEUE_ATTR)
    except Exception:
        pass
    try:
        delattr(ws, _PB_WS_CHAIN_SERIAL_LOCK_ATTR)
    except Exception:
        pass


async def _send_pb_wire_to_asr_device(
    websocket, wire: str, binaries: Optional[list[bytes]] = None, pcm: Optional[bytes] = None
) -> bool:
    """TTS 等：在仅 pb 设备连接上经队列发送，否则直接发送。返回是否完整成功。"""
    bins = list(binaries or [])
    if pcm and (not bins or bins[0] is not pcm):
        bins = [pcm] + bins
    if getattr(websocket, "_asr_chat_pb_serial_queue", False):
        _ensure_pb_device_downlink_worker(websocket)
        job = _PbDeviceJob(wire=wire, binaries=bins)
        q: asyncio.Queue = getattr(websocket, _PB_DEVICE_QUEUE_ATTR)
        await q.put(job)
        await job.done.wait()
        return bool(job.ok_json and job.ok_bins)
    ok_t, ok_b = await _safe_send_pb_json_then_binaries(websocket, wire, bins)
    return bool(ok_t and ok_b)
