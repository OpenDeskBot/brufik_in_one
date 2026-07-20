"""network_connectivity_test 工具与 pb_ack 路径的单元测试。"""
from __future__ import annotations

import asyncio
import time

import pytest

from deskbot_server.ws.pb_ack_waiter import PbAckGate


def test_pb_ack_gate_wait_idx():
    async def _run():
        gate = PbAckGate()
        device_id = "test_dev"
        req = "req001"
        await gate.begin_req(device_id, req)

        async def delayed_ack():
            await asyncio.sleep(0.05)
            await gate.notify(device_id, {"req": req, "idx": 0})

        task = asyncio.create_task(delayed_ack())
        ok = await gate.wait_idx(device_id, req, 0, timeout=2.0)
        await task
        assert ok is True

    asyncio.run(_run())


def test_pb_ack_gate_out_of_order_still_advances():
    async def _run():
        gate = PbAckGate()
        device_id = "test_dev2"
        req = "req002"
        await gate.begin_req(device_id, req)
        await gate.notify(device_id, {"req": req, "idx": 2})
        ok = await gate.wait_idx(device_id, req, 1, timeout=0.5)
        assert ok is True

    asyncio.run(_run())


def test_network_test_report_summary():
    from tools.network_connectivity_test import TestReport

    r = TestReport(device_id="d1", base_url="http://127.0.0.1:9000")
    r.ok("health")
    r.pb_ack_latencies_ms = [120.0, 180.0, 150.0]
    text = r.summary()
    assert "PASS: health" in text
    assert "p50=150" in text
    assert "全部通过" in text
