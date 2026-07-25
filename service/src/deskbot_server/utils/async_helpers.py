"""异步辅助：把阻塞调用丢到线程池，避免卡住事件循环。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


async def run_blocking(fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> R:
    """在默认线程池执行同步函数，供 Controller / Service 调用。"""
    return await asyncio.to_thread(fn, *args, **kwargs)


def spawn(coro, *, name: str | None = None) -> asyncio.Task:
    """启动后台任务，不阻塞当前协程（异常记入 Task）。"""
    task = asyncio.create_task(coro, name=name)
    return task
