"""network_connectivity_test 工具与 pb_ack 路径的单元测试。"""

from __future__ import annotations

import asyncio

from deskbot_server.ws.pb_ack_waiter import PbAckGate


def test_pb_ack_gate_wait_for_chunk_or_end():
    async def _run():
        gate = PbAckGate()
        device_id = "test_dev"
        req = "req001"
        await gate.begin_req(device_id, req)

        async def delayed_ack():
            await asyncio.sleep(0.05)
            await gate.notify(device_id, {"req": req, "idx": 9, "ack_type": "pb_chunk", "space": 40})

        task = asyncio.create_task(delayed_ack())
        chunk_ok, end_ok = await gate.wait_for_chunk_or_end(device_id, req, timeout=2.0)
        await task
        assert chunk_ok is True
        assert end_ok is False

    asyncio.run(_run())


def test_pb_ack_gate_end_received():
    """pb_end ack 同时满足 wait_for_chunk_or_end 和 wait_for_end。"""
    async def _run():
        gate = PbAckGate()
        device_id = "test_dev2"
        req = "req002"
        await gate.begin_req(device_id, req)
        await gate.notify(device_id, {"req": req, "idx": 9, "ack_type": "pb_end", "space": 40})

        chunk_ok, end_ok = await gate.wait_for_chunk_or_end(device_id, req, timeout=0.5)
        assert chunk_ok is False
        assert end_ok is True

    asyncio.run(_run())


def test_pb_ack_gate_wait_for_end():
    async def _run():
        gate = PbAckGate()
        device_id = "test_dev3"
        req = "req003"
        await gate.begin_req(device_id, req)

        async def delayed_ack():
            await asyncio.sleep(0.05)
            await gate.notify(device_id, {"req": req, "idx": 9, "ack_type": "pb_end", "space": 40})

        task = asyncio.create_task(delayed_ack())
        ok = await gate.wait_for_end(device_id, req, timeout=2.0)
        await task
        assert ok is True

    asyncio.run(_run())


def test_pb_ack_gate_chunk_consumed_on_wait():
    """wait_for_chunk_or_end 消费 chunk_received，第二次调用需新 ack。"""
    async def _run():
        gate = PbAckGate()
        device_id = "test_dev4"
        req = "req004"
        await gate.begin_req(device_id, req)
        await gate.notify(device_id, {"req": req, "idx": 9, "ack_type": "pb_chunk", "space": 40})

        chunk_ok, end_ok = await gate.wait_for_chunk_or_end(device_id, req, timeout=0.5)
        assert chunk_ok is True
        assert end_ok is False

        # 第二次无新 ack → 超时
        chunk_ok2, end_ok2 = await gate.wait_for_chunk_or_end(device_id, req, timeout=0.2)
        assert chunk_ok2 is False
        assert end_ok2 is False

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
