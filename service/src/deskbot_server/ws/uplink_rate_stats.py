"""按设备统计上行速率：每秒汇总 audio / pb_ack / camera 包数。"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass

logger = logging.getLogger("deskbot-server")


@dataclass
class _DeviceCounts:
    audio: int = 0
    ack: int = 0
    camera: int = 0


_counts: dict[str, _DeviceCounts] = defaultdict(_DeviceCounts)
_known: set[str] = set()
_ticker_task: asyncio.Task | None = None


def _key(device_id: str | None) -> str:
    return device_id if device_id else "?"


def ensure_uplink_rate_stats_started() -> None:
    """在运行中的事件循环里启动 1s 汇总任务（幂等）。"""
    global _ticker_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _ticker_task is not None and not _ticker_task.done():
        return
    _ticker_task = loop.create_task(_ticker(), name="uplink_rate_stats")


def note_uplink_audio(device_id: str | None) -> None:
    ensure_uplink_rate_stats_started()
    k = _key(device_id)
    _known.add(k)
    _counts[k].audio += 1


def note_uplink_ack(device_id: str | None) -> None:
    ensure_uplink_rate_stats_started()
    k = _key(device_id)
    _known.add(k)
    _counts[k].ack += 1


def note_uplink_camera(device_id: str | None) -> None:
    ensure_uplink_rate_stats_started()
    k = _key(device_id)
    _known.add(k)
    _counts[k].camera += 1


async def _ticker() -> None:
    global _counts
    while True:
        await asyncio.sleep(1.0)
        snap, _counts = _counts, defaultdict(_DeviceCounts)
        if not _known:
            continue
        # 已知设备每秒都打点（含 0），避免把「每 8s 一包」误读成「每秒 1～2 包」。
        parts = [
            f"{device_id}:audio={snap[device_id].audio} ack={snap[device_id].ack} cam={snap[device_id].camera}"
            for device_id in sorted(_known)
        ]
        logger.info("[uplink/1s] %s", " | ".join(parts))
