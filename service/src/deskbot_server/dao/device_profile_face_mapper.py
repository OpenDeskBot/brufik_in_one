"""人脸档案表 SQL Mapper — MyBatis 注解风格。"""

from __future__ import annotations

from deskbot_server.db.models import DeviceProfileFace
from deskbot_server.db.sql_decorators import execute, select, select_one

# ────────────────────────── 查询 ──────────────────────────


@select("SELECT * FROM device_profile_face WHERE device_id = :device_id ORDER BY id", model=DeviceProfileFace)
def list_by_device(device_id: str) -> list[DeviceProfileFace]:
    """列出设备所有人脸档案。"""


@select_one("SELECT * FROM device_profile_face WHERE id = :id", model=DeviceProfileFace)
def get_by_id(id: int) -> DeviceProfileFace | None:
    """根据主键查找人脸档案。"""


@select("SELECT * FROM device_profile_face WHERE device_id = :device_id AND name = :name", model=DeviceProfileFace)
def list_by_device_and_name(device_id: str, name: str) -> list[DeviceProfileFace]:
    """按设备 + 姓名查找档案（用于同名合并判断）。"""


@select_one("SELECT * FROM device_profile_face WHERE rowid = last_insert_rowid()", model=DeviceProfileFace)
def _last_inserted() -> DeviceProfileFace | None:
    """获取最近一次 INSERT 的行（需在同 session 中调用）。"""


# ────────────────────────── 写操作 ──────────────────────────


@execute(
    """
    INSERT INTO device_profile_face (device_id, name, descriptor, descriptor_kind, created_at, updated_at)
    VALUES (:device_id, :name, :descriptor, :descriptor_kind, datetime('now'), datetime('now'))
    """
)
def insert(device_id: str, name: str, descriptor: str, descriptor_kind: str) -> int:
    """插入新人脸档案。"""


@execute(
    """
    UPDATE device_profile_face
    SET name = :name, descriptor = :descriptor, descriptor_kind = :descriptor_kind, updated_at = datetime('now')
    WHERE id = :id
    """
)
def update(id: int, name: str, descriptor: str, descriptor_kind: str) -> int:
    """更新人脸档案（姓名 + 向量）。"""


@execute("UPDATE device_profile_face SET name = :name, updated_at = datetime('now') WHERE id = :id")
def update_name(id: int, name: str) -> int:
    """仅更新姓名。"""


@execute("DELETE FROM device_profile_face WHERE id = :id")
def delete_by_id(id: int) -> int:
    """按主键删除人脸档案。"""


@execute("DELETE FROM device_profile_face WHERE device_id = :device_id")
def delete_by_device(device_id: str) -> int:
    """删除设备所有人脸档案。"""
