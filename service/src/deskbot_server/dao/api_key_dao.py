"""API Key / 用量数据访问（单例门面）。"""

from __future__ import annotations

from typing import Any

from deskbot_server.dao import api_key_service as _aks
from deskbot_server.utils.singleton import SingletonMeta


class ApiKeyDao(metaclass=SingletonMeta):
    """对 ``dao.api_key_service`` 的单例门面。"""

    QuotaExceededError = _aks.QuotaExceededError
    ApiKeyAuth = _aks.ApiKeyAuth
    FreeApiKeyConfig = _aks.FreeApiKeyConfig
    FREE_DAILY_QUOTA_BYTES = _aks.FREE_DAILY_QUOTA_BYTES
    USAGE_CATEGORIES = _aks.USAGE_CATEGORIES

    def generate_raw_key(self, *, free: bool = False) -> str:
        return _aks.generate_raw_key(free=free)

    def create_api_key(self, user_id: str, **kwargs: Any):
        return _aks.create_api_key(user_id, **kwargs)

    def authenticate_api_key(self, raw_key: str):
        return _aks.authenticate_api_key(raw_key)

    def record_usage(self, *args: Any, **kwargs: Any):
        return _aks.record_usage(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(_aks, name)
