from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(UTC)


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
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    devices: Mapped[list[Device]] = relationship(back_populates="owner")


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("device_id", name="uq_devices_device_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    device_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    pin_code: Mapped[str | None] = mapped_column(String(4), nullable=True)
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
    __table_args__ = (UniqueConstraint("scope", "scope_key", "usage_date", name="uq_settings_test_daily"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    scope_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    usage_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
