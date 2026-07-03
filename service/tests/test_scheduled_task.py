from __future__ import annotations

import tempfile
from datetime import timedelta
from pathlib import Path

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


def test_normalize_cron_and_next_run():
    from deskbot_server.scheduled_task_service import compute_next_run, normalize_cron_expr

    assert normalize_cron_expr("0 9 * * *") == "0 9 * * *"
    nxt = compute_next_run("0 9 * * *")
    assert nxt.hour == 9
    assert nxt.minute == 0


def test_normalize_cron_repairs_llm_once_datetime():
    from deskbot_server.scheduled_task_service import normalize_cron_expr

    assert normalize_cron_expr("0 49 15 6 12") == "49 15 12 6 *"
    assert normalize_cron_expr("44 15 12 6 *") == "44 15 12 6 *"
    assert normalize_cron_expr("0 44 15 12 6 2026") == "44 15 12 6 *"


def test_create_list_delete_cron_task(temp_db):
    from deskbot_server.scheduled_task_service import (
        create_scheduled_task,
        delete_scheduled_task,
        execute_schedule_task_tool,
        list_scheduled_tasks_for_device,
    )

    row = create_scheduled_task(
        "deskbot_test",
        "提醒主人喝水",
        cron="0 9 * * *",
        task_kind="recurring",
    )
    assert row["device_id"] == "deskbot_test"
    assert row["cron_expr"] == "0 9 * * *"
    assert row["task_kind"] == "recurring"
    assert row["status"] == "active"

    listed = execute_schedule_task_tool({"action": "list"}, device_id="deskbot_test")
    assert listed["ok"] is True
    assert listed["count"] == 1

    tasks = list_scheduled_tasks_for_device("deskbot_test")
    assert tasks[0]["id"] == row["id"]

    assert delete_scheduled_task(row["id"], device_id="deskbot_test")
    assert list_scheduled_tasks_for_device("deskbot_test") == []


def test_schedule_task_crud_via_tool(temp_db):
    from deskbot_server.application.llm_tool_runner import execute_llm_tools
    from deskbot_server.scheduled_task_service import get_scheduled_task

    created = execute_llm_tools(
        [
            {
                "tool": "schedule_task",
                "action": "create",
                "task": "十分钟后提醒开会",
                "delay_minutes": 10,
                "task_kind": "once",
            }
        ],
        device_id="deskbot_a",
        session_id="sess_abc123",
    )
    assert created[0]["ok"] is True
    tid = created[0]["id"]
    assert created[0]["task_kind"] == "once"
    assert created[0]["session_id"] == "sess_abc123"

    got = execute_llm_tools(
        [{"tool": "schedule_task", "action": "get", "id": tid}],
        device_id="deskbot_a",
    )
    assert got[0]["task"]["id"] == tid

    updated = execute_llm_tools(
        [
            {
                "tool": "schedule_task",
                "action": "update",
                "id": tid,
                "task": "提醒喝水",
            }
        ],
        device_id="deskbot_a",
    )
    assert updated[0]["ok"] is True
    assert updated[0]["description"] == "提醒喝水"

    deleted = execute_llm_tools(
        [{"tool": "schedule_task", "action": "delete", "id": tid}],
        device_id="deskbot_a",
    )
    assert deleted[0]["ok"] is True
    assert get_scheduled_task(tid, device_id="deskbot_a") is None


def test_claim_due_tasks_lookback_window(temp_db):
    from deskbot_server.db.engine import get_session
    from deskbot_server.db.models import ScheduledTask, _new_id
    from deskbot_server.scheduled_task_service import (
        claim_due_tasks,
        cst_now,
        expire_overdue_active_tasks,
    )

    now = cst_now()
    session = get_session()
    session.add(
        ScheduledTask(
            id=_new_id(),
            device_id="deskbot_a",
            description="刚到期",
            cron_expr="0 9 * * *",
            task_kind="once",
            enabled=True,
            next_run_at=now - timedelta(minutes=2),
            status="active",
        )
    )
    session.add(
        ScheduledTask(
            id=_new_id(),
            device_id="deskbot_a",
            description="太久以前",
            cron_expr="0 9 * * *",
            task_kind="once",
            enabled=True,
            next_run_at=now - timedelta(minutes=10),
            status="active",
        )
    )
    session.commit()

    expired = expire_overdue_active_tasks(lookback_minutes=5)
    assert expired == 1

    claimed = claim_due_tasks(lookback_minutes=5)
    assert len(claimed) == 1
    assert claimed[0]["description"] == "刚到期"


def test_migrate_legacy_run_at_column(temp_db, monkeypatch):
    from sqlalchemy import inspect, text

    from deskbot_server.db.engine import get_session, init_engine
    from deskbot_server.db.init_db import _migrate_scheduled_tasks_drop_legacy_run_at
    from deskbot_server.scheduled_task_service import create_scheduled_task

    engine = init_engine(temp_db)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS scheduled_tasks"))
        conn.execute(
            text(
                """
                CREATE TABLE scheduled_tasks (
                    id VARCHAR(36) NOT NULL PRIMARY KEY,
                    device_id VARCHAR(128) NOT NULL,
                    description TEXT NOT NULL,
                    run_at DATETIME NOT NULL,
                    status VARCHAR(16) NOT NULL,
                    result_summary TEXT,
                    created_at DATETIME NOT NULL,
                    executed_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO scheduled_tasks (
                    id, device_id, description, run_at, status, created_at
                ) VALUES (
                    'legacy1', 'deskbot_a', '旧任务', '2026-06-12 09:00:00', 'active', '2026-06-12 08:00:00'
                )
                """
            )
        )

    _migrate_scheduled_tasks_drop_legacy_run_at(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("scheduled_tasks")}
    assert "run_at" not in cols
    assert "next_run_at" in cols

    row = create_scheduled_task("deskbot_a", "提醒喝水", cron="44 15 12 6 *", task_kind="once")
    assert row["description"] == "提醒喝水"


def test_scheduled_reminder_tts_helpers():
    from deskbot_server.application.chat_flow import (
        _scheduled_reminder_tts,
        _scheduled_task_description,
        _scheduled_tts_looks_like_meta_report,
    )

    desc = _scheduled_task_description(
        "[系统定时任务] 请向主人朗声提醒并执行以下任务：提醒喝水"
    )
    assert desc == "提醒喝水"
    assert _scheduled_reminder_tts("提醒喝水") == "主人，该喝水啦。"
    assert _scheduled_tts_looks_like_meta_report("提醒已发送，小明记得喝水哦。")
    assert not _scheduled_tts_looks_like_meta_report("该喝水啦")


def test_finish_recurring_reschedules(temp_db):
    from deskbot_server.db.engine import get_session
    from deskbot_server.db.models import ScheduledTask, _new_id
    from deskbot_server.scheduled_task_service import cst_now, finish_scheduled_task, get_scheduled_task

    tid = _new_id()
    now = cst_now()
    session = get_session()
    session.add(
        ScheduledTask(
            id=tid,
            device_id="deskbot_a",
            description="每日提醒",
            cron_expr="0 9 * * *",
            task_kind="recurring",
            enabled=True,
            next_run_at=now,
            status="running",
        )
    )
    session.commit()
    finish_scheduled_task(tid, ok=True, summary="完成")
    row = get_scheduled_task(tid, device_id="deskbot_a")
    assert row is not None
    assert row["status"] == "active"
    assert row["enabled"] is True
