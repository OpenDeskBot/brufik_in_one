"""设备表 SQL Mapper — MyBatis 注解风格。"""

from __future__ import annotations

from deskbot_server.db.models import Device
from deskbot_server.db.sql_decorators import execute, select, select_one

# ────────────────────────── 查询 ──────────────────────────


@select("SELECT * FROM devices WHERE owner_user_id = :user_id ORDER BY claimed_at DESC", model=Device)
def list_for_user(user_id: str) -> list[Device]:
    """列出用户所有设备。"""


@select_one("SELECT * FROM devices WHERE device_id = :device_id", model=Device)
def get_by_device_id(device_id: str) -> Device | None:
    """根据 device_id 查找设备。"""


@select("SELECT device_id FROM devices WHERE owner_user_id = :user_id")
def device_ids_for_user(user_id: str) -> list[str]:
    """返回用户绑定的所有 device_id。"""


# ────────────────────────── 写操作 ──────────────────────────


@execute(
    """
    INSERT INTO devices (id, device_id, owner_user_id, display_name, volume, fps, version, claimed_at, created_at)
    VALUES (:uid, :device_id, :user_id, :display_name, :volume, :fps, :version, datetime('now'), datetime('now'))
    """
)
def insert(
    uid: str,
    device_id: str,
    user_id: str,
    display_name: str,
    volume: int = 80,
    fps: int = 10,
    version: str | None = None,
) -> int:
    """插入新设备记录。"""


@execute(
    """
    UPDATE devices
    SET owner_user_id = :user_id,
        display_name  = :display_name
    WHERE id = :device_id_pk
    """
)
def update_owner(device_id_pk: str, user_id: str, display_name: str) -> int:
    """更新设备归属（转绑 / 重绑）。"""


@execute("UPDATE devices SET volume = :volume WHERE device_id = :device_id")
def update_volume(device_id: str, volume: int) -> int:
    """更新设备音量。"""


@execute("UPDATE devices SET device_id = :new_device_id WHERE device_id = :old_device_id")
def update_device_id(old_device_id: str, new_device_id: str) -> int:
    """重置设备 ID（随机后缀变更）。"""


@execute("UPDATE devices SET auto_reply = :auto_reply WHERE device_id = :device_id")
def update_auto_reply(device_id: str, auto_reply: bool) -> int:
    """更新自动应答开关。"""


@execute("UPDATE devices SET servo_mode = :servo_mode WHERE device_id = :device_id")
def update_servo_mode(device_id: str, servo_mode: str) -> int:
    """更新舵机跟随模式。"""


@execute("DELETE FROM devices WHERE device_id = :device_id AND owner_user_id = :user_id")
def delete_by_device_id(device_id: str, user_id: str) -> int:
    """删除设备绑定。"""
