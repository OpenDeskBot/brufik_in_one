"""设备对话 Session 表 SQL Mapper — MyBatis 注解风格。"""

from __future__ import annotations

from deskbot_server.db.models import DeviceSession, DeviceSessionMessage
from deskbot_server.db.sql_decorators import execute, select, select_one

# ─────────────────────── Session 查询 ───────────────────────


@select(
    "SELECT * FROM device_session WHERE device_id = :device_id ORDER BY updated_at DESC LIMIT :limit",
    model=DeviceSession,
)
def list_sessions(device_id: str, limit: int = 10) -> list[DeviceSession]:
    """列出设备最近 Session。"""


@select_one("SELECT * FROM device_session WHERE id = :id", model=DeviceSession)
def get_session(id: str) -> DeviceSession | None:
    """根据主键查找 Session。"""


@select_one(
    "SELECT * FROM device_session WHERE device_id = :device_id ORDER BY updated_at DESC LIMIT 1",
    model=DeviceSession,
)
def get_latest_session(device_id: str) -> DeviceSession | None:
    """获取设备最近一个 Session。"""


# ─────────────────────── Session 写操作 ───────────────────────


@execute(
    """
    INSERT INTO device_session (id, device_id, title, created_at, updated_at)
    VALUES (:id, :device_id, :title, datetime('now'), datetime('now'))
    """
)
def insert_session(id: str, device_id: str, title: str) -> int:
    """创建新 Session。"""


@execute("UPDATE device_session SET title = :title, updated_at = datetime('now') WHERE id = :id")
def update_session_title(id: str, title: str) -> int:
    """更新 Session 标题。"""


@execute("UPDATE device_session SET updated_at = datetime('now') WHERE id = :id")
def touch_session(id: str) -> int:
    """刷新 Session 活跃时间。"""


@execute("DELETE FROM device_session WHERE id = :id")
def delete_session(id: str) -> int:
    """删除 Session（级联消息由 DB 外键或应用层处理）。"""


# ─────────────────────── Message 查询 ───────────────────────


@select(
    "SELECT * FROM device_session_message WHERE session_id = :session_id ORDER BY id",
    model=DeviceSessionMessage,
)
def list_messages(session_id: str) -> list[DeviceSessionMessage]:
    """列出 Session 所有消息。"""


@select_one("SELECT COUNT(*) FROM device_session_message WHERE session_id = :session_id")
def count_messages(session_id: str) -> int:
    """统计 Session 消息数。"""


# ─────────────────────── Message 写操作 ───────────────────────


@execute(
    """
    INSERT INTO device_session_message (session_id, role, content, created_at)
    VALUES (:session_id, :role, :content, datetime('now'))
    """
)
def insert_message(session_id: str, role: str, content: str) -> int:
    """插入一条消息。"""


@execute("DELETE FROM device_session_message WHERE session_id = :session_id")
def delete_messages(session_id: str) -> int:
    """删除 Session 所有消息。"""
