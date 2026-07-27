"""Web 登录用户对象（FastAPI session 鉴权）。"""

from __future__ import annotations

from deskbot_server.db.models import User


class SessionUser:
    """供会话鉴权与模板使用的登录用户。"""

    def __init__(self, user: User):
        self._user = user

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_active(self) -> bool:
        return bool(self._user.is_active)

    @property
    def is_anonymous(self) -> bool:
        return False

    def get_id(self) -> str:
        return str(self._user.id)

    @property
    def id(self) -> str:
        return str(self._user.id)

    @property
    def email(self) -> str:
        return str(self._user.email)

    @property
    def display_name(self) -> str | None:
        return getattr(self._user, "display_name", None)

    @property
    def is_developer(self) -> bool:
        return bool(getattr(self._user, "is_developer", False))

    def __getattr__(self, name: str):
        return getattr(self._user, name)


# 兼容旧 import，迁移完成后可删除。