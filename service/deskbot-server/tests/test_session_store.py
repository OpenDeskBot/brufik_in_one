from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


@pytest.fixture()
def session_env(tmp_path, monkeypatch):
    from deskbot_server import device_data as dd
    from deskbot_server import session_store as ss

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(dd, "DATA_DIR", data_dir)
    monkeypatch.setattr(dd, "DEVICE_DATA_ROOT", data_dir / "device")
    monkeypatch.setattr(ss, "SESSION_IDLE_SECONDS", 600)
    return data_dir, ss


def test_create_and_append_turn(session_env):
    data_dir, ss = session_env
    dev = "deskbot_test"

    session = ss.create_session(dev, title="你好")
    assert session["session_id"]
    assert session["title"] == "你好"
    assert session["messages"] == []

    updated = ss.append_turn(dev, session["session_id"], "今天天气怎么样", "今天晴")
    assert len(updated["messages"]) == 2
    assert updated["messages"][0]["role"] == "user"
    assert updated["messages"][0]["message"] == "今天天气怎么样"
    assert updated["messages"][1]["role"] == "assistant"
    assert updated["messages"][1]["message"] == "今天晴"

    path = data_dir / "device" / dev / "session" / f"{session['session_id']}.json"
    assert path.is_file()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["title"] == "今天天气怎么样"
    assert len(raw["messages"]) == 2


def test_session_history_for_llm(session_env):
    _, ss = session_env
    dev = "deskbot_test"
    session = ss.create_session(dev)
    ss.append_turn(dev, session["session_id"], "你好", "你好呀")

    history = ss.session_history_for_llm(dev, session["session_id"])
    assert history == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好呀"},
    ]


def test_new_session_after_idle(session_env):
    _, ss = session_env
    dev = "deskbot_test"
    now = 1_000_000.0

    s1 = ss.ensure_active_session(dev, user_text="第一轮", now=now)
    ss.append_turn(dev, s1["session_id"], "第一轮", "回复一", now=now + 1)

    s2 = ss.ensure_active_session(dev, user_text="第二轮", now=now + 602)
    assert s2["session_id"] != s1["session_id"]
    assert s2["title"] == "第二轮"
    assert s2["messages"] == []


def test_continue_session_within_idle_window(session_env):
    _, ss = session_env
    dev = "deskbot_test"
    now = 2_000_000.0

    s1 = ss.ensure_active_session(dev, user_text="你好", now=now)
    ss.append_turn(dev, s1["session_id"], "你好", "你好呀", now=now + 1)

    s2 = ss.ensure_active_session(dev, user_text="再问一句", now=now + 300)
    assert s2["session_id"] == s1["session_id"]


def test_list_and_get_sessions(session_env):
    _, ss = session_env
    dev = "deskbot_test"
    now = 3_000_000.0

    s1 = ss.create_session(dev, title="旧对话", now=now)
    ss.append_turn(dev, s1["session_id"], "旧", "旧回复", now=now + 1)
    s2 = ss.create_session(dev, title="新对话", now=now + 700)

    rows = ss.list_recent_sessions(dev, limit=5)
    assert len(rows) == 2
    assert rows[0]["session_id"] == s2["session_id"]
    assert rows[0]["title"] == "新对话"
    assert rows[1]["message_count"] == 2

    current = ss.get_current_session(dev)
    assert current is not None
    assert current["session_id"] == s2["session_id"]


def test_execute_session_tool(session_env):
    _, ss = session_env
    dev = "deskbot_test"
    session = ss.create_session(dev, title="测试")
    ss.append_turn(dev, session["session_id"], "问题", "答案")

    current = ss.execute_session_tool({"action": "current"}, device_id=dev)
    assert current["ok"] is True
    assert current["session"]["session_id"] == session["session_id"]
    assert current["session"]["message_count"] == 2

    listed = ss.execute_session_tool({"action": "list"}, device_id=dev)
    assert listed["ok"] is True
    assert listed["count"] == 1

    got = ss.execute_session_tool(
        {"action": "get", "session_id": session["session_id"]},
        device_id=dev,
    )
    assert got["session"]["messages"][0]["message"] == "问题"


def test_session_tool_requires_device_id(session_env):
    _, ss = session_env
    with pytest.raises(ValueError, match="device_id"):
        ss.execute_session_tool({"action": "current"}, device_id="")


def test_llm_tool_runner_session_tool(session_env):
    from deskbot_server.application.llm_tool_runner import execute_llm_tools

    _, ss = session_env
    dev = "deskbot_test"
    session = ss.create_session(dev, title="工具测试")
    ss.append_turn(dev, session["session_id"], "你好", "嗨")

    results = execute_llm_tools(
        [{"tool": "session", "action": "current"}],
        device_id=dev,
    )
    assert len(results) == 1
    assert results[0]["ok"] is True
    assert results[0]["session"]["session_id"] == session["session_id"]
