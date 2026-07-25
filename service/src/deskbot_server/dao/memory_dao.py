"""用户记忆 JSON 数据访问。"""

from __future__ import annotations

from typing import Any, Optional

from deskbot_server.dao import memory_store as _store
from deskbot_server.utils.singleton import SingletonMeta


class MemoryDao(metaclass=SingletonMeta):
    def load_entries(self, *, device_id: Optional[str] = None) -> list[dict[str, Any]]:
        return _store.load_memory_entries(device_id=device_id)

    def save_entries(self, entries: list[dict[str, Any]], *, device_id: Optional[str] = None) -> None:
        _store.save_memory_entries(entries, device_id=device_id)

    def add(self, text: str, *, device_id: Optional[str] = None) -> dict[str, Any]:
        return _store.add_memory(text, device_id=device_id)

    def delete(self, entry_id: str, *, device_id: Optional[str] = None) -> bool:
        return _store.delete_memory(entry_id, device_id=device_id)

    def list_for_device(self, device_id: Optional[str] = None, *, limit: int | None = None) -> list[dict[str, Any]]:
        if limit is None:
            return _store.list_memory_for_device(device_id)
        return _store.list_memory_for_device(device_id, limit=limit)

    def __getattr__(self, name: str) -> Any:
        return getattr(_store, name)
