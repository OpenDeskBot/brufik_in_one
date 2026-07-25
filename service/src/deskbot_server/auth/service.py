"""用户认证业务：委托 ``UserDao``（兼容旧函数式 API）。"""

from __future__ import annotations

from deskbot_server.dao.user_dao import UserDao
from deskbot_server.db.models import User

_dao = UserDao()


def normalize_email(email: str) -> str:
    return _dao.normalize_email(email)


def validate_email(email: str) -> bool:
    return _dao.validate_email(email)


def get_user_by_email(email: str) -> User | None:
    return _dao.get_by_email(email)


def get_user_by_id(user_id: str) -> User | None:
    return _dao.get_by_id(user_id)


def create_user(email: str, password: str) -> User:
    return _dao.create(email, password)


def verify_password(user: User, password: str) -> bool:
    return _dao.verify_password(user, password)


def update_display_name(user_id: str, display_name: str) -> None:
    _dao.update_display_name(user_id, display_name)


def list_users() -> list[User]:
    return _dao.list_all()


def count_developers() -> int:
    return _dao.count_developers()


def set_user_developer(user_id: str, *, is_developer: bool) -> User:
    return _dao.set_developer(user_id, is_developer=is_developer)


def change_password(user_id: str, old_password: str, new_password: str) -> None:
    _dao.change_password(user_id, old_password, new_password)
