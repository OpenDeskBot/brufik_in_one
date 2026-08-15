"""下行 pb 链：按设备 ``pb_ack.ack_type`` 流控（pb_chunk / pb_end）。"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("deskbot-server")


def pb_wait_ack_enabled() -> bool:
    return os.environ.get("PB_WAIT_ACK", "1").strip().lower() not in ("0", "false", "no", "off")


def pb_wait_ack_timeout_sec() -> float:
    return max(0.5, float(os.environ.get("PB_WAIT_ACK_TIMEOUT_SEC", "8.0")))


@dataclass
class _ReqAckState:
    chunk_received: bool = False
    end_received: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    cond: asyncio.Condition = field(init=False)

    def __post_init__(self) -> None:
        self.cond = asyncio.Condition(self.lock)


class PbAckGate:
    """按 ``(device_id, req)`` 等待 ``pb_ack.ack_type`` 为 ``pb_chunk`` 或 ``pb_end``。"""

    def __init__(self) -> None:
        self._states: dict[tuple[str, str], _ReqAckState] = {}
        self._meta_lock = asyncio.Lock()

    async def _state(self, device_id: str, req: str) -> _ReqAckState:
        key = (device_id, req)
        async with self._meta_lock:
            st = self._states.get(key)
            if st is None:
                st = _ReqAckState()
                self._states[key] = st
            return st

    async def begin_req(self, device_id: str, req: str) -> None:
        """新一轮下发前重置该 ``req`` 的确认状态。"""
        if not device_id or not req:
            return
        async with self._meta_lock:
            self._states[(device_id, req)] = _ReqAckState()

    async def notify(self, device_id: str, ack: dict[str, Any]) -> None:
        if not device_id:
            return
        req = ack.get("req")
        if not isinstance(req, str) or not req:
            return
        ack_type = str(ack.get("ack_type", ""))
        st = await self._state(device_id, req)
        async with st.cond:
            if ack_type == "pb_chunk":
                st.chunk_received = True
            elif ack_type == "pb_end":
                st.end_received = True
            st.cond.notify_all()

    async def wait_for_chunk_or_end(self, device_id: str, req: str, *, timeout: float | None = None) -> tuple[bool, bool]:
        """等待 ``pb_chunk`` 或 ``pb_end`` ack。

        Returns ``(chunk_received, end_received)``。
        消费 ``chunk_received``（重置为 False），``end_received`` 保持 True。
        """
        if not device_id or not req:
            return True, False
        if timeout is None:
            timeout = pb_wait_ack_timeout_sec()
        st = await self._state(device_id, req)
        deadline = time.monotonic() + timeout
        async with st.cond:
            while not st.chunk_received and not st.end_received:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning(
                        "[pb_ack] 等待超时 device_id=%s req=%s",
                        device_id, req,
                    )
                    return False, False
                try:
                    await asyncio.wait_for(st.cond.wait(), timeout=remaining)
                except TimeoutError:
                    logger.warning(
                        "[pb_ack] 等待超时 device_id=%s req=%s",
                        device_id, req,
                    )
                    return False, False
            chunk = st.chunk_received
            end = st.end_received
            st.chunk_received = False  # 消费 chunk
            # end_received 保持 True（一次性事件）
        logger.info("[pb_ack] 已确认 device_id=%s req=%s chunk=%s end=%s", device_id, req, chunk, end)
        return chunk, end

    async def wait_for_end(self, device_id: str, req: str, *, timeout: float | None = None) -> bool:
        """等待 ``pb_end`` ack。"""
        if not device_id or not req:
            return True
        if timeout is None:
            timeout = pb_wait_ack_timeout_sec()
        st = await self._state(device_id, req)
        deadline = time.monotonic() + timeout
        async with st.cond:
            while not st.end_received:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning(
                        "[pb_ack] 等待 end 超时 device_id=%s req=%s",
                        device_id, req,
                    )
                    return False
                try:
                    await asyncio.wait_for(st.cond.wait(), timeout=remaining)
                except TimeoutError:
                    logger.warning(
                        "[pb_ack] 等待 end 超时 device_id=%s req=%s",
                        device_id, req,
                    )
                    return False
        logger.info("[pb_ack] end 已确认 device_id=%s req=%s", device_id, req)
        return True


pb_ack_gate = PbAckGate()
