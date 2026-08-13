from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def temp_db(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        monkeypatch.setenv("DESKBOT_DB_PATH", str(db_path))
        from deskbot_server.db import init_database
        from deskbot_server.db.engine import init_engine, reset_engine

        reset_engine()
        init_engine(db_path)
        init_database()
        yield db_path


def test_register_and_bind_device(temp_db):
    from deskbot_server.auth.device_service import user_owns_device
    from deskbot_server.auth.service import create_user
    from tests.device_bind_helpers import bind_device_online

    user = create_user("alice@example.com", "secret1234")
    assert user.is_developer is True
    device = bind_device_online(user.id, "deskbot_a1")
    assert device.device_id == "deskbot_a1"
    assert device.pin_code == "1234"
    assert user_owns_device(user.id, "deskbot_a1")


def test_bind_requires_online_device(temp_db):
    from deskbot_server.auth.device_service import bind_device
    from deskbot_server.auth.service import create_user

    user = create_user("offline@example.com", "secret1234")
    with pytest.raises(ValueError, match="绑定失败：设备未在线"):
        bind_device(user.id, "deskbot_offline", "1234")


def test_bind_rejects_wrong_pin(temp_db):
    from deskbot_server.auth.device_service import bind_device
    from deskbot_server.auth.service import create_user
    from deskbot_server.ws.device_pin import set_online_pin

    user = create_user("wrongpin@example.com", "secret1234")
    set_online_pin("deskbot_wrongpin", "1234")
    with pytest.raises(ValueError, match="绑定失败：Pin Code 不正确"):
        bind_device(user.id, "deskbot_wrongpin", "5678")


def test_bind_pin_reset_revokes_ownership(temp_db):
    from deskbot_server.auth.device_service import bind_device, device_ids_for_user, user_owns_device
    from deskbot_server.auth.service import create_user
    from deskbot_server.ws.device_pin import set_online_pin
    from tests.device_bind_helpers import bind_device_online

    user = create_user("owner@example.com", "secret1234")
    bind_device_online(user.id, "deskbot_reset", "1234")
    assert user_owns_device(user.id, "deskbot_reset")
    set_online_pin("deskbot_reset", "5678")
    assert not user_owns_device(user.id, "deskbot_reset")
    assert "deskbot_reset" in device_ids_for_user(user.id)
    other = create_user("other@example.com", "secret1234")
    bind_device(other.id, "deskbot_reset", "5678")
    assert user_owns_device(other.id, "deskbot_reset")


def test_legacy_bind_without_pin_still_owns_when_online(temp_db):
    from deskbot_server.auth.device_service import sync_device_pin_if_missing, user_owns_device
    from deskbot_server.auth.service import create_user
    from deskbot_server.db.engine import get_session
    from deskbot_server.db.models import Device
    from deskbot_server.ws.device_pin import get_online_pin, set_online_pin

    user = create_user("legacy@example.com", "secret1234")
    session = get_session()
    session.add(Device(device_id="deskbot_legacy", owner_user_id=user.id, display_name="legacy", pin_code=None))
    session.commit()
    set_online_pin("deskbot_legacy", "8082")
    assert user_owns_device(user.id, "deskbot_legacy")
    sync_device_pin_if_missing("deskbot_legacy", "8082")
    assert get_online_pin("deskbot_legacy") == "8082"
    from deskbot_server.auth.device_service import get_device_by_device_id

    assert get_device_by_device_id("deskbot_legacy").pin_code == "8082"


def test_second_user_is_not_developer_by_default(temp_db):
    from deskbot_server.auth.service import create_user

    first = create_user("first@example.com", "password1234")
    second = create_user("second@example.com", "password1234")
    assert first.is_developer is True
    assert second.is_developer is False


def test_bind_conflict(temp_db):
    from deskbot_server.auth.service import create_user
    from tests.device_bind_helpers import bind_device_online

    u1 = create_user("u1@example.com", "password123")
    u2 = create_user("u2@example.com", "password456")
    bind_device_online(u1.id, "deskbot_shared")
    with pytest.raises(ValueError, match="其他账号"):
        bind_device_online(u2.id, "deskbot_shared")


def test_fetch_live_device_details_shows_online_for_bound_device(temp_db, monkeypatch):
    import asyncio

    from deskbot_server.auth.service import create_user
    from deskbot_server.controller.runtime import set_runtime
    from deskbot_server.web.helpers import fetch_live_device_details
    from deskbot_server.ws.device_pin import set_online_pin
    from deskbot_server.ws.registry import DeviceRegistry
    from tests.device_bind_helpers import bind_device_online

    user = create_user("live@example.com", "secret1234")
    bind_device_online(user.id, "deskbot_live", "1234")
    set_online_pin("deskbot_live", "5678")

    device_registry = DeviceRegistry()

    class _FakeRuntime:
        registry = device_registry

    set_runtime(_FakeRuntime())  # type: ignore[arg-type]
    asyncio.run(device_registry.connect("deskbot_live", "asr_chat", object(), pin_code="5678"))

    live = fetch_live_device_details(user_id=user.id)
    assert live["deskbot_live"]["online"] is True
    assert live["deskbot_live"]["last_seen"] != "—"
