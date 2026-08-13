"""设备表 SQL Mapper — MyBatis 注解风格。

对比原来 device_dao.py 的写法：

    # 原来 (device_dao.py)
    def list_for_user(self, user_id: str) -> list[Device]:
        session = get_session()
        rows = session.scalars(
            select(Device).where(Device.owner_user_id == user_id)
            .order_by(Device.claimed_at.desc())
        ).all()
        for row in rows:
            session.expunge(row)
        return list(rows)

    # 现在 (device_mapper.py)
    @select("SELECT * FROM devices WHERE owner_user_id = :user_id ORDER BY claimed_at DESC", model=Device)
    def list_for_user(user_id: str) -> list[Device]:
        ...

SQL 声明在装饰器里，session/expunge 由框架处理，方法体只保留纯业务逻辑。
"""

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
    """返回用户绑定的所有 device_id（轻量查询，不加载完整 Device 对象）。"""


@select_one("SELECT COUNT(*) FROM devices WHERE owner_user_id = :user_id")
def count_for_user(user_id: str) -> int:
    """统计用户设备数。"""


@select(
    """
    SELECT d.*
    FROM devices d
    WHERE d.pin_code = :pin
      AND d.owner_user_id != :exclude_user_id
    """,
    model=Device,
)
def find_by_pin_excluding_user(pin: str, exclude_user_id: str) -> list[Device]:
    """根据 PIN 查找非指定用户的设备（用于冲突检测）。"""


# ────────────────────────── 写操作 ──────────────────────────


@execute(
    """
    INSERT INTO devices (id, device_id, pin_code, owner_user_id, display_name, claimed_at, created_at)
    VALUES (:uid, :device_id, :pin, :user_id, :display_name, datetime('now'), datetime('now'))
    """,
    model=Device,
)
def insert(uid: str, device_id: str, pin: str, user_id: str, display_name: str) -> Device:
    """插入新设备记录。"""


@execute(
    """
    UPDATE devices
    SET owner_user_id = :user_id,
        pin_code      = :pin,
        display_name  = :display_name
    WHERE id = :device_id_pk
    """,
    model=Device,
)
def update_owner(device_id_pk: str, user_id: str, pin: str, display_name: str) -> Device:
    """更新设备归属（转绑 / 重绑）。"""


@execute("UPDATE devices SET pin_code = :pin WHERE id = :device_id_pk")
def update_pin(device_id_pk: str, pin: str) -> int:
    """补写历史空 PIN。"""


@execute("DELETE FROM devices WHERE device_id = :device_id AND owner_user_id = :user_id")
def delete_by_device_id(device_id: str, user_id: str) -> int:
    """删除设备绑定，返回影响行数。"""
