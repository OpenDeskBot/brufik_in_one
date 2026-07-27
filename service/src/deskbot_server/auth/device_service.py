"""兼容旧函数式 API：委托 ``UserService``。"""

from __future__ import annotations

from deskbot_server.db.models import Device
from deskbot_server.service.user_service import UserService


def normalize_device_id(device_id: str) -> str:
    return UserService().normalize_device_id(device_id)


def validate_device_id(device_id: str) -> bool:
    return UserService().validate_device_id(device_id)


def list_devices_for_user(user_id: str) -> list[Device]:
    return UserService().list_devices(user_id)


def get_device_by_device_id(device_id: str) -> Device | None:
    return UserService().get_device(device_id)


def user_owns_device(user_id: str, device_id: str) -> bool:
    return UserService().user_owns_device(user_id, device_id)


def sync_device_pin_if_missing(device_id: str, pin_code: str) -> None:
    UserService().sync_device_pin_if_missing(device_id, pin_code)


def bind_device(
    user_id: str, device_id: str, pin_code: str, *, display_name: str | None = None
) -> Device:
    return UserService().bind_device(user_id, device_id, pin_code, display_name=display_name)


def unbind_device(user_id: str, device_id: str) -> bool:
    return UserService().unbind_device(user_id, device_id)


def device_ids_for_user(user_id: str) -> set[str]:
    return UserService().device_ids_for_user(user_id)
