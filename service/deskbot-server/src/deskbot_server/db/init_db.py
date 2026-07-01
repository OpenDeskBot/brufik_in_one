from __future__ import annotations

import logging
import threading

from deskbot_server.db.engine import init_engine
from deskbot_server.db.models import Base

logger = logging.getLogger("deskbot-server")
_seed_lock = threading.Lock()


def _migrate_legacy_schema(engine) -> None:
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "users" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("users")}
    with engine.begin() as conn:
        if "display_name" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN display_name VARCHAR(64)"))
            cols.add("display_name")
        if "is_developer" not in cols:
            if "is_builtin" in cols:
                conn.execute(text("ALTER TABLE users RENAME COLUMN is_builtin TO is_developer"))
            else:
                conn.execute(text(
                    "ALTER TABLE users ADD COLUMN is_developer BOOLEAN NOT NULL DEFAULT 0"
                ))
    _migrate_scheduled_tasks_schema(engine)


def _migrate_scheduled_tasks_schema(engine) -> None:
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "scheduled_tasks" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("scheduled_tasks")}
    stmts: list[str] = []
    if "cron_expr" not in cols:
        stmts.append("ALTER TABLE scheduled_tasks ADD COLUMN cron_expr VARCHAR(128)")
    if "task_kind" not in cols:
        stmts.append("ALTER TABLE scheduled_tasks ADD COLUMN task_kind VARCHAR(16)")
    if "enabled" not in cols:
        stmts.append("ALTER TABLE scheduled_tasks ADD COLUMN enabled BOOLEAN DEFAULT 1")
    if "next_run_at" not in cols:
        stmts.append("ALTER TABLE scheduled_tasks ADD COLUMN next_run_at DATETIME")
    if "session_id" not in cols:
        stmts.append("ALTER TABLE scheduled_tasks ADD COLUMN session_id VARCHAR(36)")
    if stmts:
        with engine.begin() as conn:
            for sql in stmts:
                conn.execute(text(sql))
        insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("scheduled_tasks")}
    with engine.begin() as conn:
        if "run_at" in cols and "next_run_at" in cols:
            conn.execute(
                text(
                    "UPDATE scheduled_tasks SET next_run_at = run_at "
                    "WHERE next_run_at IS NULL AND run_at IS NOT NULL"
                )
            )
        if "cron_expr" in cols:
            conn.execute(
                text(
                    "UPDATE scheduled_tasks SET cron_expr = '* * * * *' "
                    "WHERE cron_expr IS NULL OR cron_expr = ''"
                )
            )
        if "task_kind" in cols:
            conn.execute(
                text(
                    "UPDATE scheduled_tasks SET task_kind = 'once' "
                    "WHERE task_kind IS NULL OR task_kind = ''"
                )
            )
        if "enabled" in cols:
            conn.execute(
                text("UPDATE scheduled_tasks SET enabled = 1 WHERE enabled IS NULL")
            )
        conn.execute(
            text(
                "UPDATE scheduled_tasks SET status = 'active' "
                "WHERE status = 'pending'"
            )
        )
    _migrate_scheduled_tasks_drop_legacy_run_at(engine)


def _migrate_scheduled_tasks_drop_legacy_run_at(engine) -> None:
    """旧表含 ``run_at NOT NULL`` 而新模型只用 ``next_run_at``，需重建表避免 INSERT 失败。"""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "scheduled_tasks" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("scheduled_tasks")}
    if "run_at" not in cols:
        return

    logger.info("迁移 scheduled_tasks：移除遗留 run_at 列，统一使用 next_run_at")
    cron_sel = (
        "COALESCE(NULLIF(cron_expr, ''), '* * * * *')"
        if "cron_expr" in cols
        else "'* * * * *'"
    )
    kind_sel = (
        "COALESCE(NULLIF(task_kind, ''), 'once')"
        if "task_kind" in cols
        else "'once'"
    )
    enabled_sel = "COALESCE(enabled, 1)" if "enabled" in cols else "1"
    if "next_run_at" in cols and "run_at" in cols:
        next_run_sel = "COALESCE(next_run_at, run_at)"
    elif "next_run_at" in cols:
        next_run_sel = "next_run_at"
    else:
        next_run_sel = "run_at"
    session_sel = "session_id" if "session_id" in cols else "NULL"

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE scheduled_tasks_new (
                    id VARCHAR(36) NOT NULL PRIMARY KEY,
                    device_id VARCHAR(128) NOT NULL,
                    description TEXT NOT NULL,
                    cron_expr VARCHAR(128) NOT NULL DEFAULT '* * * * *',
                    task_kind VARCHAR(16) NOT NULL DEFAULT 'once',
                    enabled BOOLEAN NOT NULL DEFAULT 1,
                    next_run_at DATETIME NOT NULL,
                    session_id VARCHAR(36),
                    status VARCHAR(16) NOT NULL DEFAULT 'active',
                    result_summary TEXT,
                    created_at DATETIME NOT NULL,
                    executed_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                f"""
                INSERT INTO scheduled_tasks_new (
                    id, device_id, description, cron_expr, task_kind, enabled,
                    next_run_at, session_id, status, result_summary, created_at, executed_at
                )
                SELECT
                    id,
                    device_id,
                    description,
                    {cron_sel},
                    {kind_sel},
                    {enabled_sel},
                    {next_run_sel},
                    {session_sel},
                    CASE WHEN status = 'pending' THEN 'active' ELSE status END,
                    result_summary,
                    created_at,
                    executed_at
                FROM scheduled_tasks
                """
            )
        )
        conn.execute(text("DROP TABLE scheduled_tasks"))
        conn.execute(text("ALTER TABLE scheduled_tasks_new RENAME TO scheduled_tasks"))
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_scheduled_tasks_device_id "
                "ON scheduled_tasks (device_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_scheduled_tasks_status "
                "ON scheduled_tasks (status)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_scheduled_tasks_next_run_at "
                "ON scheduled_tasks (next_run_at)"
            )
        )


def init_database() -> None:
    engine = init_engine()
    _migrate_legacy_schema(engine)
    Base.metadata.create_all(bind=engine)
    _migrate_scheduled_tasks_schema(engine)
    _seed_free_api_key()


def _seed_free_api_key() -> None:
    from deskbot_server.auth.api_key_service import (
        ensure_free_usage_placeholder,
        generate_raw_key,
        read_free_api_key_config,
        write_free_api_key_file,
    )

    with _seed_lock:
        if read_free_api_key_config() is None:
            raw = generate_raw_key(free=True)
            write_free_api_key_file(raw)
            logger.warning(
                "已创建免费 API Key（每日 1GB 配额）prefix=%s —— 完整 Key 见 data/.free_api_key",
                raw[:12],
            )
        ensure_free_usage_placeholder()
