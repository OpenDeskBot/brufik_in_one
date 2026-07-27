"""用户与设备绑定服务：注册/登录、资料、设备列表与绑定。"""

from __future__ import annotations

from deskbot_server.dao.device_dao import DeviceDao
from deskbot_server.dao.user_dao import UserDao
from deskbot_server.db.models import Device, User
from deskbot_server.utils.singleton import SingletonMeta


class UserService(metaclass=SingletonMeta):
    def __init__(self) -> None:
        self._users = UserDao()
        self._devices = DeviceDao()

    # ---- 用户 ----

    def normalize_email(self, email: str) -> str:
        return self._users.normalize_email(email)

    def validate_email(self, email: str) -> bool:
        return self._users.validate_email(email)

    def register(self, email: str, password: str) -> User:
        return self._users.create(email, password)

    def login(self, email: str, password: str) -> User:
        user = self._users.get_by_email(email)
        if user is None or not user.is_active or not self._users.verify_password(user, password):
            raise ValueError("邮箱或密码错误")
        return user

    def get_user(self, user_id: str) -> User | None:
        return self._users.get_by_id(user_id)

    def get_user_by_email(self, email: str) -> User | None:
        return self._users.get_by_email(email)

    def user_info(self, user_id: str) -> dict:
        user = self._users.get_by_id(user_id)
        if user is None:
            raise ValueError("用户不存在")
        return {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name or "",
            "is_developer": bool(user.is_developer),
        }

    def update_display_name(self, user_id: str, display_name: str) -> None:
        self._users.update_display_name(user_id, display_name)

    def change_password(self, user_id: str, old_password: str, new_password: str) -> None:
        self._users.change_password(user_id, old_password, new_password)

    def verify_password(self, user: User, password: str) -> bool:
        return self._users.verify_password(user, password)

    def list_users(self) -> list[User]:
        return self._users.list_all()

    def count_developers(self) -> int:
        return self._users.count_developers()

    def set_developer(self, user_id: str, *, is_developer: bool) -> User:
        return self._users.set_developer(user_id, is_developer=is_developer)

    # ---- 设备 ----

    def normalize_device_id(self, device_id: str) -> str:
        return self._devices.normalize_device_id(device_id)

    def validate_device_id(self, device_id: str) -> bool:
        return self._devices.validate_device_id(device_id)

    def list_devices(self, user_id: str) -> list[Device]:
        return self._devices.list_for_user(user_id)

    def get_device(self, device_id: str) -> Device | None:
        return self._devices.get_by_device_id(device_id)

    def user_owns_device(self, user_id: str, device_id: str) -> bool:
        return self._devices.user_owns(user_id, device_id)

    def bind_device(
        self, user_id: str, device_id: str, pin_code: str, *, display_name: str | None = None
    ) -> Device:
        return self._devices.bind(user_id, device_id, pin_code, display_name=display_name)

    def unbind_device(self, user_id: str, device_id: str) -> bool:
        return self._devices.unbind(user_id, device_id)

    def device_ids_for_user(self, user_id: str) -> set[str]:
        return self._devices.device_ids_for_user(user_id)

    def sync_device_pin_if_missing(self, device_id: str, pin_code: str) -> None:
        self._devices.sync_pin_if_missing(device_id, pin_code)
