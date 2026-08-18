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
    from deskbot_server.service.user_service import UserService
    from tests.device_bind_helpers import bind_device_online

    svc = UserService()
    svc.register("alice@example.com", "secret1234")
    user = svc.get_user_by_email("alice@example.com")
    assert user is not None
    assert user.is_developer
    device = bind_device_online(user.id, "deskbot_a1")
    assert device.device_id == "deskbot_a1"
    assert svc.user_owns_device(user.id, "deskbot_a1")


def test_bind_requires_online_device(temp_db):
    from deskbot_server.service.user_service import UserService

    svc = UserService()
    svc.register("offline@example.com", "secret1234")
    user = svc.get_user_by_email("offline@example.com")
    assert user is not None
    with pytest.raises(ValueError, match="绑定失败：设备未在线"):
        svc.bind_device(user.id, "deskbot_offline")


def test_second_user_is_not_developer_by_default(temp_db):
    from deskbot_server.service.user_service import UserService

    svc = UserService()
    svc.register("first@example.com", "password1234")
    svc.register("second@example.com", "password1234")
    first = svc.get_user_by_email("first@example.com")
    second = svc.get_user_by_email("second@example.com")
    assert first is not None
    assert second is not None
    assert first.is_developer
    assert not second.is_developer


def test_bind_conflict(temp_db):
    from deskbot_server.service.user_service import UserService
    from tests.device_bind_helpers import bind_device_online

    svc = UserService()
    svc.register("u1@example.com", "password123")
    svc.register("u2@example.com", "password456")
    u1 = svc.get_user_by_email("u1@example.com")
    u2 = svc.get_user_by_email("u2@example.com")
    assert u1 is not None
    assert u2 is not None
    bind_device_online(u1.id, "deskbot_shared")
    with pytest.raises(ValueError, match="其他账号"):
        bind_device_online(u2.id, "deskbot_shared")


def test_fetch_live_device_details_shows_online_for_bound_device(temp_db, monkeypatch):
    import asyncio

    from deskbot_server.controller.runtime import set_runtime
    from deskbot_server.service.user_service import UserService
    from deskbot_server.web.helpers import fetch_live_device_details
    from deskbot_server.service.device_ws_service import DeviceWsService
    from tests.device_bind_helpers import bind_device_online

    svc = UserService()
    svc.register("live@example.com", "secret1234")
    user = svc.get_user_by_email("live@example.com")
    assert user is not None
    bind_device_online(user.id, "deskbot_live")

    svc = DeviceWsService.instance()
    if svc is not None:
        svc._mark_device_online("deskbot_live")
        asyncio.run(svc.register("deskbot_live", object()))

    class _FakeRuntime:
        device_ws = svc

    set_runtime(_FakeRuntime())  # type: ignore[arg-type]

    live = fetch_live_device_details(user_id=user.id)
    assert live["deskbot_live"]["online"] is True
    assert live["deskbot_live"]["last_seen"] != "—"
