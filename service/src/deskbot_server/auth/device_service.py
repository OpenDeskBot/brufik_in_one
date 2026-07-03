from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from deskbot_server.db.engine import get_session
from deskbot_server.db.models import Device

_DEVICE_ID_RE = re.compile(r"^[a-zA-Z0-9_.\-]{1,128}$")


def normalize_device_id(device_id: str) -> str:
    return (device_id or "").strip()


def validate_device_id(device_id: str) -> bool:
    return bool(_DEVICE_ID_RE.match(normalize_device_id(device_id)))


def list_devices_for_user(user_id: str) -> list[Device]:
    session = get_session()
    rows = session.scalars(
        select(Device)
        .where(Device.owner_user_id == user_id)
        .order_by(Device.claimed_at.desc())
    ).all()
    for row in rows:
        session.expunge(row)
    return list(rows)


def get_device_by_device_id(device_id: str) -> Device | None:
    session = get_session()
    row = session.scalar(select(Device).where(Device.device_id == normalize_device_id(device_id)))
    if row is not None:
        session.expunge(row)
    return row


def user_owns_device(user_id: str, device_id: str) -> bool:
    dev = get_device_by_device_id(device_id)
    return dev is not None and dev.owner_user_id == user_id


def bind_device(user_id: str, device_id: str, *, display_name: str | None = None) -> Device:
    did = normalize_device_id(device_id)
    if not validate_device_id(did):
        raise ValueError("device_id 格式无效（允许字母数字 _ . -）")

    session = get_session()
    try:
        existing = session.scalar(select(Device).where(Device.device_id == did))
        if existing is not None:
            if existing.owner_user_id != user_id:
                raise ValueError("该设备已被其他账号绑定")
            if display_name:
                existing.display_name = display_name.strip() or None
            session.commit()
            session.refresh(existing)
            session.expunge(existing)
            from deskbot_server.device_data import ensure_device_data_initialized

            ensure_device_data_initialized(existing.device_id)
            return existing

        device = Device(
            device_id=did,
            owner_user_id=user_id,
            display_name=(display_name or did).strip() or did,
        )
        session.add(device)
        session.commit()
        session.refresh(device)
        session.expunge(device)
        from deskbot_server.device_data import ensure_device_data_initialized

        ensure_device_data_initialized(device.device_id)
        return device
    except IntegrityError as exc:
        session.rollback()
        raise ValueError("绑定失败，请重试") from exc


def unbind_device(user_id: str, device_id: str) -> bool:
    session = get_session()
    row = session.scalar(
        select(Device).where(
            Device.device_id == normalize_device_id(device_id),
            Device.owner_user_id == user_id,
        )
    )
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def device_ids_for_user(user_id: str) -> set[str]:
    return {d.device_id for d in list_devices_for_user(user_id)}
