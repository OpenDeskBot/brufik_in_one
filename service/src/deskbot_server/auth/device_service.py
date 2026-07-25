"""设备绑定业务：委托 ``DeviceDao``（兼容旧函数式 API）。"""

from __future__ import annotations

from deskbot_server.dao.device_dao import DeviceDao
from deskbot_server.db.models import Device

_dao = DeviceDao()


def normalize_device_id(device_id: str) -> str:
    return _dao.normalize_device_id(device_id)


def validate_device_id(device_id: str) -> bool:
    return _dao.validate_device_id(device_id)


def list_devices_for_user(user_id: str) -> list[Device]:
    return _dao.list_for_user(user_id)


def get_device_by_device_id(device_id: str) -> Device | None:
    return _dao.get_by_device_id(device_id)


def user_owns_device(user_id: str, device_id: str) -> bool:
    return _dao.user_owns(user_id, device_id)


def bind_device(user_id: str, device_id: str, *, display_name: str | None = None) -> Device:
    return _dao.bind(user_id, device_id, display_name=display_name)


def unbind_device(user_id: str, device_id: str) -> bool:
    return _dao.unbind(user_id, device_id)


def device_ids_for_user(user_id: str) -> set[str]:
    return _dao.device_ids_for_user(user_id)
