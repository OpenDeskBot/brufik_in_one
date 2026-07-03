from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import select

from deskbot_server.db.engine import get_session
from deskbot_server.db.models import SettingsTestDaily

SETTINGS_TEST_DAILY_LIMIT = 50


class SettingsTestLimitExceeded(Exception):
    def __init__(self, scope: str, *, limit: int = SETTINGS_TEST_DAILY_LIMIT):
        self.scope = scope
        self.limit = limit
        label = "该账号" if scope == "user" else "该 IP"
        super().__init__(f"{label}今日测试次数已达上限（{limit} 次/天），请明天再试")


@dataclass(frozen=True)
class SettingsTestQuotaSnapshot:
    user_count: int
    ip_count: int
    limit: int = SETTINGS_TEST_DAILY_LIMIT

    @property
    def user_remaining(self) -> int:
        return max(0, self.limit - self.user_count)

    @property
    def ip_remaining(self) -> int:
        return max(0, self.limit - self.ip_count)


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def normalize_client_ip(raw: str | None) -> str:
    ip = (raw or "").strip()
    if not ip:
        return "unknown"
    return ip[:128]


def client_ip_from_request(req) -> str:
    forwarded = (req.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return normalize_client_ip(forwarded or req.remote_addr)


def _get_count(session, scope: str, scope_key: str, usage_date: date) -> int:
    row = session.scalar(
        select(SettingsTestDaily).where(
            SettingsTestDaily.scope == scope,
            SettingsTestDaily.scope_key == scope_key,
            SettingsTestDaily.usage_date == usage_date,
        )
    )
    return int(row.count if row else 0)


def get_settings_test_quota(*, user_id: str, client_ip: str, usage_date: date | None = None) -> SettingsTestQuotaSnapshot:
    today = usage_date or _utc_today()
    session = get_session()
    ip_key = normalize_client_ip(client_ip)
    return SettingsTestQuotaSnapshot(
        user_count=_get_count(session, "user", user_id, today),
        ip_count=_get_count(session, "ip", ip_key, today),
    )


def check_and_consume_settings_test(*, user_id: str, client_ip: str) -> SettingsTestQuotaSnapshot:
    """校验并消耗一次设置测试配额（用户与 IP 各计一次）。"""
    today = _utc_today()
    ip_key = normalize_client_ip(client_ip)
    session = get_session()

    checks = [("user", user_id), ("ip", ip_key)]
    for scope, key in checks:
        if _get_count(session, scope, key, today) >= SETTINGS_TEST_DAILY_LIMIT:
            raise SettingsTestLimitExceeded(scope)

    snapshot = SettingsTestQuotaSnapshot(
        user_count=_get_count(session, "user", user_id, today),
        ip_count=_get_count(session, "ip", ip_key, today),
    )

    for scope, key in checks:
        row = session.scalar(
            select(SettingsTestDaily).where(
                SettingsTestDaily.scope == scope,
                SettingsTestDaily.scope_key == key,
                SettingsTestDaily.usage_date == today,
            )
        )
        if row is None:
            row = SettingsTestDaily(scope=scope, scope_key=key, usage_date=today, count=1)
            session.add(row)
        else:
            row.count = int(row.count) + 1

    session.commit()

    return SettingsTestQuotaSnapshot(
        user_count=min(snapshot.user_count + 1, SETTINGS_TEST_DAILY_LIMIT + 1),
        ip_count=min(snapshot.ip_count + 1, SETTINGS_TEST_DAILY_LIMIT + 1),
    )
