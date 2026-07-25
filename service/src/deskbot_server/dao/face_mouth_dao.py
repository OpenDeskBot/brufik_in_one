"""口型 / phoneme 配置数据访问。"""

from __future__ import annotations

from typing import Any, Optional

from deskbot_server.dao import face_mouth_config_store as _store
from deskbot_server.utils.singleton import SingletonMeta


class FaceMouthDao(metaclass=SingletonMeta):
    def load(self, *, seed_if_missing: bool = True, device_id: Optional[str] = None) -> Optional[list[dict[str, Any]]]:
        return _store.load_face_mouth_cfg_file(seed_if_missing=seed_if_missing, device_id=device_id)

    def save(self, groups: list[dict[str, Any]], *, device_id: Optional[str] = None) -> None:
        _store.save_face_mouth_cfg_file(groups, device_id=device_id)

    def normalize_groups(self, raw: object) -> list[dict[str, Any]]:
        return _store.normalize_face_mouth_groups(raw)

    def groups_to_mouth_bundle(self, groups: list[dict[str, Any]]) -> dict[str, Any]:
        return _store.groups_to_mouth_bundle(groups)

    def __getattr__(self, name: str) -> Any:
        return getattr(_store, name)
