from __future__ import annotations

import asyncio


def test_publish_auto_dispatch_event():
    from deskbot_server.ws.device_pipeline import DevicePipelineBroker, publish_auto_dispatch_event

    async def _run() -> None:
        broker = DevicePipelineBroker(max_events=10)
        await publish_auto_dispatch_event(
            broker,
            device_id="dev1",
            request_id="req_auto_1",
            source="auto_idle_silence",
            summary="idle 低头沉默 舵机 (90, 80)",
        )
        items = broker.snapshot_events("dev1")
        assert len(items) == 1
        evt = items[0]
        assert evt["source"] == "auto_idle_silence"
        assert evt["request_id"] == "__auto_idle_silence__:dev1"
        assert evt["llm_text"] == evt["summary"]
        assert evt["status"] == "ok"

        await publish_auto_dispatch_event(
            broker,
            device_id="dev1",
            request_id="req_auto_2",
            source="auto_idle_silence",
            summary="idle 低头沉默 舵机 (90, 80) 更新",
        )
        items = broker.snapshot_events("dev1")
        assert len(items) == 1
        assert items[0]["summary"] == "idle 低头沉默 舵机 (90, 80) 更新"

        await publish_auto_dispatch_event(
            broker,
            device_id="dev1",
            request_id="req_chat_1",
            source="asr",
            summary="用户说了 hello",
        )
        await publish_auto_dispatch_event(
            broker,
            device_id="dev1",
            request_id="req_auto_3",
            source="auto_idle_silence",
            summary="idle 再次",
        )
        items = broker.snapshot_events("dev1")
        assert len(items) == 2
        assert items[0]["source"] == "auto_idle_silence"
        assert items[1]["source"] == "asr"

    asyncio.run(_run())


def test_pipeline_upsert_by_request_id():
    from deskbot_server.ws.device_pipeline import DevicePipelineBroker

    async def _run() -> None:
        broker = DevicePipelineBroker(max_events=10)
        await broker.publish(
            {
                "device_id": "dev1",
                "request_id": "req1",
                "source": "asr",
                "asr_text": "你好",
                "status": "running",
                "stage": "asr_done",
            }
        )
        await broker.publish(
            {
                "device_id": "dev1",
                "request_id": "req1",
                "source": "asr",
                "asr_text": "你好",
                "llm_text": "执行工具: capture_camera",
                "status": "running",
                "stage": "llm_tool_1",
            }
        )
        items = broker.snapshot_events("dev1")
        assert len(items) == 1
        assert items[0]["stage"] == "llm_tool_1"
        assert items[0]["llm_text"] == "执行工具: capture_camera"

    asyncio.run(_run())
