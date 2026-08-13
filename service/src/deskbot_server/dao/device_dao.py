"""设备表数据访问 — V2（Mapper + 业务逻辑分离）。

对比 V1 (device_dao.py)：

    V1: 业务逻辑和 SQL 查询混在同一个方法里
    ┌──────────────────────────────────────────────────┐
    │  def bind(self, user_id, device_id, pin, ...):   │
    │      # 1. 参数校验                                │
    │      # 2. 在线 PIN 检查                            │
    │      # 3. SQL 查询 + ORM 操作                      │  ← 混在一起
    │      # 4. session.commit / expunge                │
    │      # 5. 初始化设备数据                            │
    └──────────────────────────────────────────────────┘

    V2: SQL 全部提取到 mapper，DAO 只保留业务编排
    ┌──────────────────────────────────────────────────┐
    │  device_mapper.get_by_device_id(did)             │  ← SQL 在 mapper
    │  device_mapper.update_owner(pk, uid, pin, name)  │
    │  device_mapper.insert(uid, did, pin, uid, name)  │
    └──────────────────────────────────────────────────┘
    ┌──────────────────────────────────────────────────┐
    │  def bind(self, user_id, device_id, pin, ...):   │
    │      # 1. 参数校验                                │  ← 纯业务逻辑
    │      # 2. 在线 PIN 检查                            │
    │      # 3. 调用 mapper                             │
    │      # 4. 初始化设备数据                            │
    └──────────────────────────────────────────────────┘
"""

from __future__ import annotations

import re

from deskbot_server.dao import device_mapper
from deskbot_server.utils.pin_code import normalize_pin_code, validate_pin_code
from deskbot_server.utils.singleton import SingletonMeta
from deskbot_server.ws.device_pin import get_online_pin

_DEVICE_ID_RE = re.compile(r"^[a-zA-Z0-9_.\-]{1,128}$")


class DeviceDao(metaclass=SingletonMeta):
    # ────────────── 参数规范化 ──────────────

    def normalize_device_id(self, device_id: str) -> str:
        return (device_id or "").strip()

    def validate_device_id(self, device_id: str) -> bool:
        return bool(_DEVICE_ID_RE.match(self.normalize_device_id(device_id)))

    # ────────────── 查询（委托 mapper）──────────────

    def list_for_user(self, user_id: str):
        return device_mapper.list_for_user(user_id)

    def get_by_device_id(self, device_id: str):
        return device_mapper.get_by_device_id(self.normalize_device_id(device_id))

    def device_ids_for_user(self, user_id: str) -> set[str]:
        rows = device_mapper.device_ids_for_user(user_id)
        # mapper 返回 list[str]（device_id 列），转为 set
        return {r if isinstance(r, str) else r["device_id"] for r in rows}

    # ────────────── 业务逻辑 ──────────────

    def user_owns(self, user_id: str, device_id: str) -> bool:
        """DB 归属为准；仅当绑定 PIN 与在线 PIN 均有效且不一致时视为已失权。"""
        dev = self.get_by_device_id(device_id)
        if dev is None or dev.owner_user_id != user_id:
            return False
        stored_pin = normalize_pin_code(dev.pin_code)
        online_pin = get_online_pin(device_id)
        if validate_pin_code(stored_pin) and online_pin and online_pin != stored_pin:
            return False
        return True

    def sync_pin_if_missing(self, device_id: str, pin_code: str) -> None:
        """设备上线时补写历史空 PIN。"""
        did = self.normalize_device_id(device_id)
        pin = normalize_pin_code(pin_code)
        if not did or not validate_pin_code(pin):
            return
        row = device_mapper.get_by_device_id(did)
        if row is None:
            return
        stored = normalize_pin_code(row.pin_code)
        if validate_pin_code(stored):
            return
        device_mapper.update_pin(row.id, pin)

    def bind(self, user_id: str, device_id: str, pin_code: str, *, display_name: str | None = None):
        """绑定设备到用户。"""
        from deskbot_server.db.models import _new_id
        from deskbot_server.utils.device_data import ensure_device_data_initialized

        did = self.normalize_device_id(device_id)
        pin = normalize_pin_code(pin_code)
        if not self.validate_device_id(did):
            raise ValueError("device_id 格式无效（允许字母数字 _ . -）")
        if not validate_pin_code(pin):
            raise ValueError("PIN Code 格式无效（4 位数字，1000–9999）")

        online_pin = get_online_pin(did)
        if not online_pin:
            raise ValueError("绑定失败：设备未在线，请确认设备已开机并连接 Wi‑Fi")
        if online_pin != pin:
            raise ValueError("绑定失败：Pin Code 不正确")

        existing = device_mapper.get_by_device_id(did)
        if existing is not None:
            stored_pin = normalize_pin_code(existing.pin_code)
            if existing.owner_user_id != user_id and stored_pin == online_pin:
                raise ValueError("该设备已被其他账号绑定")
            name = (display_name or "").strip() or existing.display_name or did
            device = device_mapper.update_owner(existing.id, user_id, pin, name)
            ensure_device_data_initialized(did, pin)
            return device

        name = (display_name or did).strip() or did
        device = device_mapper.insert(_new_id(), did, pin, user_id, name)
        ensure_device_data_initialized(did, pin)
        return device

    def unbind(self, user_id: str, device_id: str) -> bool:
        """解除设备绑定。"""
        affected = device_mapper.delete_by_device_id(self.normalize_device_id(device_id), user_id)
        return affected > 0
