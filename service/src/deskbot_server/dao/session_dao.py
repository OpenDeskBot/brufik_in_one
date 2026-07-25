"""对话 Session JSON 数据访问。"""

from __future__ import annotations

from typing import Any

from deskbot_server.dao import session_store as _store
from deskbot_server.utils.singleton import SingletonMeta


class SessionDao(metaclass=SingletonMeta):
    def load(self, device_id: str, session_id: str) -> dict[str, Any] | None:
        return _store.load_session(device_id, session_id)

    def save(self, session: dict[str, Any]) -> None:
        _store.save_session(session)

    def create(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return _store.create_session(*args, **kwargs)

    def ensure_active(self, device_id: str, **kwargs: Any) -> dict[str, Any]:
        return _store.ensure_active_session(device_id, **kwargs)

    def history_for_llm(self, device_id: str, **kwargs: Any) -> list[dict[str, str]]:
        return _store.session_history_for_llm(device_id, **kwargs)

    def append_turn(self, *args: Any, **kwargs: Any) -> Any:
        return _store.append_turn(*args, **kwargs)

    def list_recent(self, device_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
        return _store.list_recent_sessions(device_id, limit=limit)

    def get_current(self, device_id: str) -> dict[str, Any] | None:
        return _store.get_current_session(device_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(_store, name)
