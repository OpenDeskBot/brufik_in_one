from __future__ import annotations

from flask_login import UserMixin

from deskbot_server.db.models import User


class FlaskUser(UserMixin):
    def __init__(self, user: User):
        self._user = user

    @property
    def id(self) -> str:
        return self._user.id

    @property
    def email(self) -> str:
        return self._user.email

    @property
    def display_name(self) -> str:
        name = (self._user.display_name or "").strip()
        if name:
            return name
        # 未设置显示名称时，用邮箱 @ 前缀代替完整邮箱，问候语/导航栏更友好
        return self.email.split("@", 1)[0] or self.email

    @property
    def is_developer(self) -> bool:
        return bool(getattr(self._user, "is_developer", False))

    @property
    def db_user(self) -> User:
        return self._user
