"""AsrChatHub attach：同 device 仅保留最新 /asr_chat 连接。"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from deskbot_server.ws.asr_chat_hub import AsrChatHub


def test_attach_closes_previous_connection_for_same_device():
    async def _run() -> None:
        hub = AsrChatHub(device_pb_only=True)
        old_ws = MagicMock()
        old_ws.close = AsyncMock()
        new_ws = MagicMock()

        await hub.attach("dev1", old_ws)
        await hub.attach("dev1", new_ws)

        async with hub._lock:
            conns = hub._by_device.get("dev1", set())
        assert conns == {new_ws}
        old_ws.close.assert_awaited_once()
        assert old_ws.close.await_args.kwargs.get("code") == 1000

    asyncio.run(_run())


def test_attach_keeps_only_one_ws_in_hub():
    async def _run() -> None:
        hub = AsrChatHub(device_pb_only=True)
        ws_a = MagicMock()
        ws_a.close = AsyncMock()
        ws_b = MagicMock()
        ws_b.close = AsyncMock()
        ws_c = MagicMock()
        ws_c.close = AsyncMock()

        await hub.attach("dev1", ws_a)
        await hub.attach("dev1", ws_b)
        await hub.attach("dev1", ws_c)

        async with hub._lock:
            conns = hub._by_device.get("dev1", set())
        assert conns == {ws_c}
        assert ws_a.close.await_count == 1
        assert ws_b.close.await_count == 1
        ws_c.close.assert_not_awaited()

    asyncio.run(_run())
