"""用户表数据访问。"""

from __future__ import annotations

import re

from werkzeug.security import check_password_hash, generate_password_hash

from deskbot_server.dao import user_mapper
from deskbot_server.db.models import User, _new_id
from deskbot_server.utils.singleton import SingletonMeta

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class UserDao(metaclass=SingletonMeta):
    def normalize_email(self, email: str) -> str:
        return (email or "").strip().lower()

    def validate_email(self, email: str) -> bool:
        return bool(_EMAIL_RE.match(self.normalize_email(email)))

    def get_by_email(self, email: str) -> User | None:
        return user_mapper.get_by_email(self.normalize_email(email))

    def get_by_id(self, user_id: str) -> User | None:
        return user_mapper.get_by_id(user_id)

    def create(self, email: str, password: str) -> User:
        email_norm = self.normalize_email(email)
        if not self.validate_email(email_norm):
            raise ValueError("邮箱格式无效")
        if len(password) < 8:
            raise ValueError("密码至少 8 位")

        is_first_user = not user_mapper.has_any_user()
        try:
            user = user_mapper.create(_new_id(), email_norm, generate_password_hash(password), is_first_user)
        except Exception as exc:
            err = str(exc).lower()
            if "email" in err or "unique" in err:
                raise ValueError("该邮箱已注册") from exc
            raise ValueError("注册失败，请稍后重试") from exc
        return user

    def verify_password(self, user: User, password: str) -> bool:
        return check_password_hash(user.password_hash, password)

    def update_display_name(self, user_id: str, display_name: str) -> None:
        name = (display_name or "").strip()[:64]
        if not name:
            raise ValueError("用户名称不能为空")
        user = self.get_by_id(user_id)
        if user is None:
            raise ValueError("用户不存在")
        user_mapper.update_display_name(user_id, name)

    def list_all(self) -> list[User]:
        return user_mapper.list_all()

    def count_developers(self) -> int:
        return user_mapper.count_developers(is_dev=True)

    def set_developer(self, user_id: str, *, is_developer: bool) -> User:
        user = self.get_by_id(user_id)
        if user is None:
            raise ValueError("用户不存在")
        if user.is_developer and not is_developer and self.count_developers() <= 1:
            raise ValueError("至少保留一名开发者")
        user_mapper.set_developer(user_id, is_developer)
        user.is_developer = is_developer
        return user

    def change_password(self, user_id: str, old_password: str, new_password: str) -> None:
        if len(new_password) < 8:
            raise ValueError("新密码至少 8 位")
        user = self.get_by_id(user_id)
        if user is None:
            raise ValueError("用户不存在")
        if not check_password_hash(user.password_hash, old_password):
            raise ValueError("旧密码错误")
        user_mapper.update_password(user_id, generate_password_hash(new_password))
