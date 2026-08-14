from __future__ import annotations

import logging

from deskbot_server.db.engine import init_engine
from deskbot_server.db.models import Base

logger = logging.getLogger("deskbot-server")


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
                conn.execute(text("ALTER TABLE users ADD COLUMN is_developer BOOLEAN NOT NULL DEFAULT 0"))


def _migrate_devices_schema(engine) -> None:
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "devices" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("devices")}
    with engine.begin() as conn:
        if "volume" not in cols:
            conn.execute(text("ALTER TABLE devices ADD COLUMN volume INTEGER NOT NULL DEFAULT 80"))
        if "fps" not in cols:
            conn.execute(text("ALTER TABLE devices ADD COLUMN fps INTEGER NOT NULL DEFAULT 10"))
        if "version" not in cols:
            conn.execute(text("ALTER TABLE devices ADD COLUMN version VARCHAR(16)"))
        if "auto_reply" not in cols:
            conn.execute(text("ALTER TABLE devices ADD COLUMN auto_reply BOOLEAN NOT NULL DEFAULT 1"))
        if "servo_mode" not in cols:
            conn.execute(text("ALTER TABLE devices ADD COLUMN servo_mode VARCHAR(16) NOT NULL DEFAULT ''"))


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
                text("UPDATE scheduled_tasks SET next_run_at = run_at WHERE next_run_at IS NULL AND run_at IS NOT NULL")
            )
        if "cron_expr" in cols:
            conn.execute(
                text("UPDATE scheduled_tasks SET cron_expr = '* * * * *' WHERE cron_expr IS NULL OR cron_expr = ''")
            )
        if "task_kind" in cols:
            conn.execute(
                text("UPDATE scheduled_tasks SET task_kind = 'once' WHERE task_kind IS NULL OR task_kind = ''")
            )
        if "enabled" in cols:
            conn.execute(text("UPDATE scheduled_tasks SET enabled = 1 WHERE enabled IS NULL"))
        conn.execute(text("UPDATE scheduled_tasks SET status = 'active' WHERE status = 'pending'"))
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
    cron_sel = "COALESCE(NULLIF(cron_expr, ''), '* * * * *')" if "cron_expr" in cols else "'* * * * *'"
    kind_sel = "COALESCE(NULLIF(task_kind, ''), 'once')" if "task_kind" in cols else "'once'"
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
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_scheduled_tasks_device_id ON scheduled_tasks (device_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_scheduled_tasks_status ON scheduled_tasks (status)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_scheduled_tasks_next_run_at ON scheduled_tasks (next_run_at)"))


def _migrate_face_profiles_to_db(engine) -> None:
    """将旧 JSON 人脸档案（data/{device_id}/face_profiles.json）导入 device_profile_face 表。"""
    import json

    from sqlalchemy import inspect, text

    from deskbot_server.utils.paths import DATA_DIR

    insp = inspect(engine)
    if "device_profile_face" not in insp.get_table_names():
        return

    # 表中已有数据则跳过迁移
    with engine.begin() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM device_profile_face")).scalar()
    if count and count > 0:
        return

    # 扫描 data/*/face_profiles.json（排除 global/）
    json_files = list(DATA_DIR.glob("*/face_profiles.json"))
    if not json_files:
        return

    migrated = 0
    for jf in json_files:
        device_id = jf.parent.name
        if device_id == "global":
            continue
        try:
            raw = json.loads(jf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        items = raw.get("profiles") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            desc_raw = item.get("descriptor")
            if not name or not isinstance(desc_raw, list) or len(desc_raw) < 4:
                continue
            descriptor = json.dumps([float(x) for x in desc_raw], ensure_ascii=False)
            kind = str(item.get("descriptor_kind") or "embedding").strip()
            try:
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            "INSERT INTO device_profile_face "
                            "(device_id, name, descriptor, descriptor_kind, created_at, updated_at) "
                            "VALUES (:did, :name, :desc, :kind, datetime('now'), datetime('now'))"
                        ),
                        {"did": device_id, "name": name, "desc": descriptor, "kind": kind},
                    )
                migrated += 1
            except Exception:
                logger.warning("迁移 face_profiles 失败: device_id=%s name=%s", device_id, name, exc_info=True)

    if migrated:
        logger.info("已迁移 %d 条人脸档案到 device_profile_face 表", migrated)


def _migrate_memory_to_db(engine) -> None:
    """将旧 JSON 记忆（data/{device_id}/user_memory.json）导入 device_memory 表。"""
    import json

    from sqlalchemy import inspect, text

    from deskbot_server.utils.paths import DATA_DIR

    insp = inspect(engine)
    if "device_memory" not in insp.get_table_names():
        return
    with engine.begin() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM device_memory")).scalar()
    if count and count > 0:
        return

    json_files = list(DATA_DIR.glob("*/user_memory.json"))
    if not json_files:
        return

    migrated = 0
    for jf in json_files:
        device_id = jf.parent.name
        if device_id == "global":
            continue
        try:
            raw = json.loads(jf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        items = raw.get("entries") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            text_val = str(item.get("text") or item.get("value") or "").strip()
            if not text_val:
                continue
            title = str(item.get("title") or text_val[:64] or "").strip() or "未命名"
            try:
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            "INSERT OR IGNORE INTO device_memory "
                            "(device_id, title, parent, text, created_at, updated_at) "
                            "VALUES (:did, :title, '', :text, datetime('now'), datetime('now'))"
                        ),
                        {"did": device_id, "title": title, "text": text_val},
                    )
                migrated += 1
            except Exception:
                logger.warning("迁移 memory 失败: device_id=%s title=%s", device_id, title, exc_info=True)

    if migrated:
        logger.info("已迁移 %d 条记忆到 device_memory 表", migrated)


def _migrate_sessions_to_db(engine) -> None:
    """将旧 JSON Session（data/{device_id}/session/*.json）导入 device_session + device_session_message 表。"""
    import json

    from sqlalchemy import inspect, text

    from deskbot_server.utils.paths import DATA_DIR

    insp = inspect(engine)
    tables = insp.get_table_names()
    if "device_session" not in tables or "device_session_message" not in tables:
        return
    with engine.begin() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM device_session")).scalar()
    if count and count > 0:
        return

    session_dirs = list(DATA_DIR.glob("*/session"))
    if not session_dirs:
        return

    migrated = 0
    for sdir in session_dirs:
        device_id = sdir.parent.name
        if device_id == "global":
            continue
        for jf in sorted(sdir.glob("*.json")):
            if jf.name == "_meta.json":
                continue
            try:
                raw = json.loads(jf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            sid = str(raw.get("session_id") or jf.stem).strip()
            title = str(raw.get("title") or "新对话").strip()[:64]
            created = raw.get("created_at")
            updated = raw.get("updated_at")
            if not sid:
                continue
            try:
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            "INSERT OR IGNORE INTO device_session "
                            "(id, device_id, title, created_at, updated_at) "
                            "VALUES (:sid, :did, :title, "
                            "COALESCE(:created, datetime('now')), COALESCE(:updated, datetime('now')))"
                        ),
                        {"sid": sid, "did": device_id, "title": title, "created": created, "updated": updated},
                    )
                    for msg in raw.get("messages") or []:
                        if not isinstance(msg, dict):
                            continue
                        role = str(msg.get("role") or "").strip()
                        content = str(msg.get("message") or msg.get("content") or "").strip()
                        if role not in ("user", "assistant") or not content:
                            continue
                        conn.execute(
                            text(
                                "INSERT INTO device_session_message "
                                "(session_id, role, content, created_at) "
                                "VALUES (:sid, :role, :content, COALESCE(:ts, datetime('now')))"
                            ),
                            {"sid": sid, "role": role, "content": content, "ts": msg.get("ts")},
                        )
                migrated += 1
            except Exception:
                logger.warning("迁移 session 失败: device_id=%s sid=%s", device_id, sid, exc_info=True)

    if migrated:
        logger.info("已迁移 %d 个 Session 到 device_session 表", migrated)


def init_database() -> None:
    engine = init_engine()
    _migrate_legacy_schema(engine)
    Base.metadata.create_all(bind=engine)
    _migrate_devices_schema(engine)
    _migrate_scheduled_tasks_schema(engine)
    _migrate_face_profiles_to_db(engine)
    _migrate_memory_to_db(engine)
    _migrate_sessions_to_db(engine)
