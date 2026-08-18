"""WS 发送工具：fire-and-forget 广播。"""

from __future__ import annotations

import asyncio

from deskbot_server.utils.ws_utils import WsUtils


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
