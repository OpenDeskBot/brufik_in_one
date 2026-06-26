from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


@pytest.fixture()
def temp_db(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        monkeypatch.setenv("DESKBOT_DB_PATH", str(db_path))
        from deskbot_server.db.engine import init_engine, reset_engine
        from deskbot_server.db import init_database

        reset_engine()
        init_engine(db_path)
        init_database()
        yield db_path


def test_complete_llm_with_tool_loop_two_rounds(temp_db):
    from deskbot_server.application.llm_tool_loop import complete_llm_with_tool_loop

    round1 = json.dumps(
        {"tts": "", "tools": [{"tool": "memory_add", "text": "喜欢猫"}], "moves": [], "anims": []},
        ensure_ascii=False,
    )
    round2 = json.dumps(
        {"tts": "已记住你喜欢猫", "tools": [], "moves": [], "anims": []},
        ensure_ascii=False,
    )

    chat = AsyncMock()
    chat.llm = AsyncMock(side_effect=[round1, round2])

    async def _run():
        return await complete_llm_with_tool_loop(
            chat,
            "记住我喜欢猫",
            device_id="deskbot_a",
            request_id="req1",
        )

    parsed, tools, results, raw = asyncio.run(_run())
    assert parsed["reply"] == "已记住你喜欢猫"
    assert len(tools) == 1
    assert tools[0]["tool"] == "memory_add"
    assert len(results) == 1
    assert results[0]["ok"] is True
    assert chat.llm.call_count == 2
    assert raw == round2


def test_complete_llm_with_tool_loop_single_round():
    from deskbot_server.application.llm_tool_loop import complete_llm_with_tool_loop

    answer = json.dumps({"tts": "你好", "tools": [], "moves": [], "anims": []})

    class _FakeChat:
        async def llm(
            self,
            text,
            *,
            device_context=None,
            device_id=None,
            history_messages=None,
            extra_messages=None,
            on_tts_ready=None,
        ):
            return answer

    async def _run():
        return await complete_llm_with_tool_loop(
            _FakeChat(),
            "你好",
            device_id="deskbot_a",
        )

    parsed, tools, results, _raw = asyncio.run(_run())
    assert parsed["reply"] == "你好"
    assert tools == []
    assert results == []
