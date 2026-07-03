from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_developer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    devices: Mapped[list[Device]] = relationship(back_populates="owner")
    api_keys: Mapped[list[ApiKey]] = relationship(back_populates="user")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="default")
    key_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    is_free: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    daily_quota_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User | None] = relationship(back_populates="api_keys")
    usage_rows: Mapped[list[UsageDaily]] = relationship(back_populates="api_key")


class UsageDaily(Base):
    __tablename__ = "usage_daily"
    __table_args__ = (UniqueConstraint("api_key_id", "usage_date", name="uq_usage_api_key_date"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    api_key_id: Mapped[str] = mapped_column(String(36), ForeignKey("api_keys.id"), nullable=False, index=True)
    usage_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    asr_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    face_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    llm_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    tts_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    api_key: Mapped[ApiKey] = relationship(back_populates="usage_rows")

    @property
    def total_bytes(self) -> int:
        return int(self.asr_bytes + self.face_bytes + self.llm_bytes + self.tts_bytes)


class UsageDailyDevice(Base):
    __tablename__ = "usage_daily_device"
    __table_args__ = (
        UniqueConstraint("api_key_id", "device_id", "usage_date", name="uq_usage_device_key_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    api_key_id: Mapped[str] = mapped_column(String(36), ForeignKey("api_keys.id"), nullable=False, index=True)
    device_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    usage_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    asr_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    face_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    llm_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    tts_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    @property
    def total_bytes(self) -> int:
        return int(self.asr_bytes + self.face_bytes + self.llm_bytes + self.tts_bytes)


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("device_id", name="uq_devices_device_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    device_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    owner_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    access_token_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    owner: Mapped[User] = relationship(back_populates="devices")


class ScheduledTask(Base):
    """设备 cron 定时任务：由 LLM tools 创建，调度器按 next_run_at 触发。"""

    __tablename__ = "scheduled_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    device_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    cron_expr: Mapped[str] = mapped_column(String(128), nullable=False, default="* * * * *")
    task_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="once")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SettingsTestDaily(Base):
    """设置页 LLM/TTS 测试每日配额（按用户与 IP 分别计数）。"""

    __tablename__ = "settings_test_daily"
    __table_args__ = (
        UniqueConstraint("scope", "scope_key", "usage_date", name="uq_settings_test_daily"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    scope_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    usage_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
