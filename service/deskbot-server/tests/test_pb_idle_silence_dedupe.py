"""pb_idle_silence 去重逻辑单元测试。"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from deskbot_server.ws.asr_chat_hub import AsrChatHub, PbIdleSilenceServoAfterDownlink


def _hub():
    h = MagicMock(spec=AsrChatHub)
    h.first_ws = AsyncMock(return_value=object())
    h.pipeline_broker = None
    h._skip_idle_note_once = set()

    async def _send(device_id, payload, *, skip_idle_refresh=False):
        if skip_idle_refresh:
            h._skip_idle_note_once.add(device_id)
        return 1

    h.send = AsyncMock(side_effect=_send)
    h.consume_skip_idle_note = AsrChatHub.consume_skip_idle_note.__get__(h, AsrChatHub)
    return h


def test_skip_repeat_until_other_activity():
    hub = _hub()
    sched = PbIdleSilenceServoAfterDownlink(hub, idle_sec=0.01)
    device_id = "deskbot_test"

    async def _run() -> None:
        await sched._deliver_silence_servo(device_id)
        assert device_id in sched._silence_already_sent
        hub.send.assert_awaited_once()

        await sched._deliver_silence_servo(device_id)
        hub.send.assert_awaited_once()

        sched.note_activity(device_id)
        assert device_id not in sched._silence_already_sent

        await sched._deliver_silence_servo(device_id)
        assert hub.send.await_count == 2

    asyncio.run(_run())


def test_hub_skip_idle_note_once():
    hub = AsrChatHub(device_pb_only=True)
    assert not hub.consume_skip_idle_note("dev1")
    hub._skip_idle_note_once.add("dev1")
    assert hub.consume_skip_idle_note("dev1")
    assert not hub.consume_skip_idle_note("dev1")
