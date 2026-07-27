"""兼容旧函数式 API：委托 ``UserService``。"""

from __future__ import annotations

from deskbot_server.db.models import User
from deskbot_server.service.user_service import UserService


def normalize_email(email: str) -> str:
    return UserService().normalize_email(email)


def validate_email(email: str) -> bool:
    return UserService().validate_email(email)


def get_user_by_email(email: str) -> User | None:
    return UserService().get_user_by_email(email)


def get_user_by_id(user_id: str) -> User | None:
    return UserService().get_user(user_id)


def create_user(email: str, password: str) -> User:
    return UserService().register(email, password)


def verify_password(user: User, password: str) -> bool:
    return UserService().verify_password(user, password)


def update_display_name(user_id: str, display_name: str) -> None:
    UserService().update_display_name(user_id, display_name)


def list_users() -> list[User]:
    return UserService().list_users()


def count_developers() -> int:
    return UserService().count_developers()


def set_user_developer(user_id: str, *, is_developer: bool) -> User:
    return UserService().set_developer(user_id, is_developer=is_developer)


def change_password(user_id: str, old_password: str, new_password: str) -> None:
    UserService().change_password(user_id, old_password, new_password)
