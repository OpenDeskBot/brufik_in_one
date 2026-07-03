"""进程内重 CPU 任务的并发上限（asyncio.Semaphore）。"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

logger = logging.getLogger("deskbot-server")

_asr_sem: Optional[asyncio.Semaphore] = None
_face_sem: Optional[asyncio.Semaphore] = None


def _resolve_limit(cfg_val: int, *, env_name: str, default_when_zero: int) -> Optional[int]:
    raw_env = (os.environ.get(env_name) or "").strip()
    if raw_env:
        try:
            n = int(raw_env)
        except ValueError:
            n = cfg_val
    else:
        n = int(cfg_val or 0)
    if n <= 0:
        n = default_when_zero
    return n if n > 0 else None


def configure_concurrency(
    *,
    max_concurrent_asr: int = 0,
    max_concurrent_face_infer: int = 0,
) -> None:
    """在 ``main`` 启动时调用一次；``0`` 表示使用与 CPU 核数相关的默认值。"""
    global _asr_sem, _face_sem
    cpu = os.cpu_count() or 2
    asr_n = _resolve_limit(
        max_concurrent_asr,
        env_name="DESKBOT_MAX_CONCURRENT_ASR",
        default_when_zero=max(1, min(4, cpu)),
    )
    face_n = _resolve_limit(
        max_concurrent_face_infer,
        env_name="DESKBOT_MAX_CONCURRENT_FACE",
        default_when_zero=max(2, min(8, cpu * 2)),
    )
    _asr_sem = asyncio.Semaphore(asr_n) if asr_n else None
    _face_sem = asyncio.Semaphore(face_n) if face_n else None
    logger.info(
        "[concurrency] max_concurrent_asr=%s max_concurrent_face_infer=%s (cpu=%d)",
        asr_n if asr_n else "unlimited",
        face_n if face_n else "unlimited",
        cpu,
    )


@asynccontextmanager
async def asr_infer_slot():
    if _asr_sem is None:
        yield
        return
    async with _asr_sem:
        yield


@asynccontextmanager
async def face_infer_slot():
    if _face_sem is None:
        yield
        return
    async with _face_sem:
        yield
