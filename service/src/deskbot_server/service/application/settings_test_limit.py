"""设置页测试每日配额（按 device_id 计数）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select

from deskbot_server.db.engine import get_session
from deskbot_server.db.models import DeviceUsage

SETTINGS_TEST_DAILY_LIMIT = 50


class SettingsTestLimitExceeded(Exception):
    def __init__(self, device_id: str, *, limit: int = SETTINGS_TEST_DAILY_LIMIT):
        self.device_id = device_id
        self.limit = limit
        super().__init__(f"设备 {device_id} 今日测试次数已达上限（{limit} 次/天），请明天再试")


@dataclass(frozen=True)
class SettingsTestQuotaSnapshot:
    count: int
    limit: int = SETTINGS_TEST_DAILY_LIMIT

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.count)


def _utc_today() -> date:
    return datetime.now(UTC).date()


def _get_count(device_id: str, usage_date: date) -> int:
    session = get_session()
    row = session.scalar(select(DeviceUsage).where(DeviceUsage.device_id == device_id, DeviceUsage.date == usage_date))
    return int(row.cv if row else 0)


def get_settings_test_quota(*, device_id: str, usage_date: date | None = None) -> SettingsTestQuotaSnapshot:
    today = usage_date or _utc_today()
    return SettingsTestQuotaSnapshot(count=_get_count(device_id, today))


def check_and_consume_settings_test(*, device_id: str) -> SettingsTestQuotaSnapshot:
    """校验并消耗一次设置测试配额（按 device_id 计数，复用 cv 字段）。"""
    today = _utc_today()
    session = get_session()

    row = session.scalar(select(DeviceUsage).where(DeviceUsage.device_id == device_id, DeviceUsage.date == today))
    current = int(row.cv if row else 0)
    if current >= SETTINGS_TEST_DAILY_LIMIT:
        raise SettingsTestLimitExceeded(device_id)

    if row is None:
        row = DeviceUsage(device_id=device_id, date=today, cv=1, total=1)
        session.add(row)
    else:
        row.cv = current + 1
        row.total = int(row.total) + 1

    session.commit()
    return SettingsTestQuotaSnapshot(count=current + 1)
