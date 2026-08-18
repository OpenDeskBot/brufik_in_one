"""设备长期记忆表 SQL Mapper — MyBatis 注解风格。

同时提供兼容旧 ``memory_store.py`` 的高层 API（add_memory / list_memory_for_device 等）。
"""

from __future__ import annotations

import uuid
from typing import Any

from deskbot_server.db.models import DeviceMemory
from deskbot_server.db.sql_decorators import execute, select, select_one

# ────────────────────────── 底层 Mapper ──────────────────────────


@select(
    "SELECT * FROM device_memory WHERE device_id = :device_id ORDER BY created_at DESC",
    model=DeviceMemory,
)
def list_by_device(device_id: str) -> list[DeviceMemory]:
    """列出设备所有记忆条目。"""


@select("SELECT * FROM device_memory ORDER BY created_at DESC", model=DeviceMemory)
def list_all() -> list[DeviceMemory]:
    """列出所有记忆条目。"""


@select_one("SELECT * FROM device_memory WHERE id = :id", model=DeviceMemory)
def get_by_id(id: int) -> DeviceMemory | None:
    """根据主键查找记忆。"""


@select_one(
    "SELECT * FROM device_memory WHERE device_id = :device_id AND title = :title",
    model=DeviceMemory,
)
def get_by_device_and_title(device_id: str, title: str) -> DeviceMemory | None:
    """按设备 + 标题查找记忆。"""


@execute(
    """
    INSERT OR IGNORE INTO device_memory (device_id, title, parent, text, created_at, updated_at)
    VALUES (:device_id, :title, :parent, :text, datetime('now'), datetime('now'))
    """
)
def insert(device_id: str, title: str, parent: str, text: str) -> int:
    """插入新记忆条目（同 device_id+title 已存在时忽略）。"""


@execute(
    """
    UPDATE device_memory
    SET title = :title, parent = :parent, text = :text, updated_at = datetime('now')
    WHERE id = :id
    """
)
def update(id: int, title: str, parent: str, text: str) -> int:
    """更新记忆（标题 + 目录 + 内容）。"""


@execute("UPDATE device_memory SET text = :text, updated_at = datetime('now') WHERE id = :id")
def update_text(id: int, text: str) -> int:
    """仅更新记忆内容。"""


@execute("UPDATE device_memory SET retrieved_at = datetime('now') WHERE id = :id")
def touch_retrieved(id: int) -> int:
    """更新最后检索时间。"""


@execute("DELETE FROM device_memory WHERE id = :id")
def delete_by_id(id: int) -> int:
    """按主键删除记忆。"""


@execute("DELETE FROM device_memory WHERE device_id = :device_id")
def delete_by_device(device_id: str) -> int:
    """删除设备所有记忆。"""


# ────────────────────────── 高层 API（兼容旧 memory_store 接口）──────────────────────────

_MAX_PROMPT_ENTRIES = 30


def _row_to_dict(row: DeviceMemory) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "device_id": row.device_id,
        "title": row.title,
        "parent": row.parent,
        "text": row.text,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def list_memory_for_device(device_id: str | None = None, *, limit: int = _MAX_PROMPT_ENTRIES) -> list[dict[str, Any]]:
    """列出设备记忆，返回 dict 列表（兼容旧 memory_store.list_memory_for_device）。"""
    dev = str(device_id or "").strip()
    if dev:
        rows = list_by_device(dev)
    else:
        rows = list_all()
    return [_row_to_dict(r) for r in rows[:limit]]


def add_memory(text: str, *, device_id: str | None = None) -> dict[str, Any]:
    """添加一条记忆，返回 dict（兼容旧 memory_store.add_memory）。"""
    dev = str(device_id or "").strip()
    title = uuid.uuid4().hex[:12]
    insert(dev, title, "", str(text or "").strip())
    row = get_by_device_and_title(dev, title)
    return _row_to_dict(row) if row else {"id": "", "text": text}


def get_memory(entry_id: str, *, device_id: str | None = None) -> dict[str, Any] | None:
    """获取单条记忆（兼容旧 memory_store.get_memory）。"""
    try:
        row = get_by_id(int(entry_id))
    except (ValueError, TypeError):
        return None
    if row is None:
        return None
    dev = str(device_id or "").strip()
    if dev and row.device_id != dev:
        return None
    return _row_to_dict(row)


def update_memory(entry_id: str, text: str, *, device_id: str | None = None) -> dict[str, Any] | None:
    """更新记忆内容（兼容旧 memory_store.update_memory）。"""
    try:
        row_id = int(entry_id)
    except (ValueError, TypeError):
        return None
    row = get_by_id(row_id)
    if row is None:
        return None
    dev = str(device_id or "").strip()
    if dev and row.device_id != dev:
        return None
    update_text(row_id, str(text or "").strip())
    row = get_by_id(row_id)
    return _row_to_dict(row) if row else None


def delete_memory(entry_id: str, *, device_id: str | None = None) -> bool:
    """删除记忆（兼容旧 memory_store.delete_memory）。"""
    try:
        row_id = int(entry_id)
    except (ValueError, TypeError):
        return False
    row = get_by_id(row_id)
    if row is None:
        return False
    dev = str(device_id or "").strip()
    if dev and row.device_id != dev:
        return False
    delete_by_id(row_id)
    return True
