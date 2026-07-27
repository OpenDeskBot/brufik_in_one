"""设备表数据访问。"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from deskbot_server.db.engine import get_session
from deskbot_server.db.models import Device
from deskbot_server.utils.singleton import SingletonMeta
from deskbot_server.ws.device_pin import get_online_pin, normalize_pin_code, validate_pin_code

_DEVICE_ID_RE = re.compile(r"^[a-zA-Z0-9_.\-]{1,128}$")


class DeviceDao(metaclass=SingletonMeta):
    def normalize_device_id(self, device_id: str) -> str:
        return (device_id or "").strip()

    def validate_device_id(self, device_id: str) -> bool:
        return bool(_DEVICE_ID_RE.match(self.normalize_device_id(device_id)))

    def list_for_user(self, user_id: str) -> list[Device]:
        session = get_session()
        rows = session.scalars(
            select(Device).where(Device.owner_user_id == user_id).order_by(Device.claimed_at.desc())
        ).all()
        for row in rows:
            session.expunge(row)
        return list(rows)

    def get_by_device_id(self, device_id: str) -> Device | None:
        session = get_session()
        row = session.scalar(select(Device).where(Device.device_id == self.normalize_device_id(device_id)))
        if row is not None:
            session.expunge(row)
        return row

    def user_owns(self, user_id: str, device_id: str) -> bool:
        """DB 归属为准；仅当绑定 PIN 与在线 PIN 均有效且不一致时视为已失权（设备已重置 PIN）。"""
        dev = self.get_by_device_id(device_id)
        if dev is None or dev.owner_user_id != user_id:
            return False
        stored_pin = normalize_pin_code(dev.pin_code)
        online_pin = get_online_pin(device_id)
        # 历史绑定可能尚未写入 pin_code：仍认 DB 归属，避免后台下发全被 forbidden_device
        if validate_pin_code(stored_pin) and online_pin and online_pin != stored_pin:
            return False
        return True

    def sync_pin_if_missing(self, device_id: str, pin_code: str) -> None:
        """设备上线时补写历史空 PIN，便于后续 PIN 重置失权逻辑生效。"""
        did = self.normalize_device_id(device_id)
        pin = normalize_pin_code(pin_code)
        if not did or not validate_pin_code(pin):
            return
        session = get_session()
        row = session.scalar(select(Device).where(Device.device_id == did))
        if row is None:
            return
        stored = normalize_pin_code(row.pin_code)
        if validate_pin_code(stored):
            return
        row.pin_code = pin
        session.commit()

    def bind(self, user_id: str, device_id: str, pin_code: str, *, display_name: str | None = None) -> Device:
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

        session = get_session()
        try:
            existing = session.scalar(select(Device).where(Device.device_id == did))
            if existing is not None:
                stored_pin = normalize_pin_code(existing.pin_code)
                if existing.owner_user_id != user_id and stored_pin == online_pin:
                    raise ValueError("该设备已被其他账号绑定")
                existing.owner_user_id = user_id
                existing.pin_code = pin
                if display_name:
                    existing.display_name = display_name.strip() or None
                elif not existing.display_name:
                    existing.display_name = did
                session.commit()
                session.refresh(existing)
                session.expunge(existing)
                from deskbot_server.utils.device_data import ensure_device_data_initialized

                ensure_device_data_initialized(existing.device_id, pin)
                return existing

            device = Device(
                device_id=did,
                pin_code=pin,
                owner_user_id=user_id,
                display_name=(display_name or did).strip() or did,
            )
            session.add(device)
            session.commit()
            session.refresh(device)
            session.expunge(device)
            from deskbot_server.utils.device_data import ensure_device_data_initialized

            ensure_device_data_initialized(device.device_id, pin)
            return device
        except IntegrityError as exc:
            session.rollback()
            raise ValueError("绑定失败，请重试") from exc

    def unbind(self, user_id: str, device_id: str) -> bool:
        session = get_session()
        row = session.scalar(
            select(Device).where(
                Device.device_id == self.normalize_device_id(device_id), Device.owner_user_id == user_id
            )
        )
        if row is None:
            return False
        session.delete(row)
        session.commit()
        return True

    def device_ids_for_user(self, user_id: str) -> set[str]:
        """账号已绑定的 device_id（仅 DB 归属，不含在线 PIN 校验）。"""
        return {d.device_id for d in self.list_for_user(user_id)}
