"""下行 pb 链：按设备 ``pb_ack`` 流控（收齐 idx 后再发下一片）。"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("deskbot-server")


def pb_wait_ack_enabled() -> bool:
    return os.environ.get("PB_WAIT_ACK", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def pb_wait_ack_timeout_sec() -> float:
    return max(0.5, float(os.environ.get("PB_WAIT_ACK_TIMEOUT_SEC", "8.0")))


@dataclass
class _ReqAckState:
    last_idx: int = -1
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    cond: asyncio.Condition = field(init=False)

    def __post_init__(self) -> None:
        self.cond = asyncio.Condition(self.lock)


class PbAckGate:
    """按 ``(device_id, req)`` 等待 ``pb_ack.idx >= 目标 idx``。"""

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
        """新一轮下发前重置该 ``req`` 的确认水位。"""
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
        try:
            idx = int(ack.get("idx", -1))
        except (TypeError, ValueError):
            idx = -1
        st = await self._state(device_id, req)
        async with st.cond:
            if idx > st.last_idx:
                st.last_idx = idx
            st.cond.notify_all()

    async def wait_idx(
        self,
        device_id: str,
        req: str,
        idx: int,
        *,
        timeout: Optional[float] = None,
    ) -> bool:
        if not device_id or not req:
            return True
        if timeout is None:
            timeout = pb_wait_ack_timeout_sec()
        st = await self._state(device_id, req)
        deadline = time.monotonic() + timeout
        async with st.cond:
            while st.last_idx < idx:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning(
                        "[pb_ack] 等待超时 device_id=%s req=%s need_idx>=%s last_idx=%s",
                        device_id,
                        req,
                        idx,
                        st.last_idx,
                    )
                    return False
                try:
                    await asyncio.wait_for(st.cond.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    logger.warning(
                        "[pb_ack] 等待超时 device_id=%s req=%s need_idx>=%s last_idx=%s",
                        device_id,
                        req,
                        idx,
                        st.last_idx,
                    )
                    return False
        logger.info(
            "[pb_ack] 已确认 device_id=%s req=%s need_idx=%s last_idx=%s",
            device_id,
            req,
            idx,
            st.last_idx,
        )
        return True


pb_ack_gate = PbAckGate()
