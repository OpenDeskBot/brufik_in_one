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
        return (self._user.display_name or "").strip() or self.email

    @property
    def is_developer(self) -> bool:
        return bool(getattr(self._user, "is_developer", False))

    @property
    def db_user(self) -> User:
        return self._user
