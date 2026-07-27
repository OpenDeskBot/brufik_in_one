from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


def _read_free_key_from_file(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("api_key="):
            return line.split("=", 1)[1].strip()
    raise AssertionError(f"api_key not found in {path}")


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


def test_free_api_key_seed(temp_db):
    from deskbot_server.dao.api_key_service import (
        FREE_DAILY_QUOTA_BYTES,
        FREE_FILE_KEY_ID,
        authenticate_api_key,
        read_free_api_key_config,
    )

    key_file = temp_db.parent / ".free_api_key"
    assert key_file.is_file()
    cfg = read_free_api_key_config()
    assert cfg is not None
    assert cfg.daily_quota_bytes == FREE_DAILY_QUOTA_BYTES
    auth = authenticate_api_key(cfg.api_key)
    assert auth is not None
    assert auth.is_free is True
    assert auth.api_key_id == FREE_FILE_KEY_ID


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


def test_api_key_create_auth_and_usage(temp_db):
    from deskbot_server.auth.service import create_user, update_display_name
    from deskbot_server.dao.api_key_service import (
        authenticate_api_key,
        create_api_key,
        get_user_usage_summary,
        record_usage,
    )
    from tests.device_bind_helpers import bind_device_online

    user = create_user("bob@example.com", "password1234")
    update_display_name(user.id, "Bob")
    bind_device_online(user.id, "deskbot_bob")
    raw, row = create_api_key(user.id, name="dev")
    auth = authenticate_api_key(raw)
    assert auth is not None
    assert auth.user_id == user.id
    assert auth.is_free is False

    record_usage(row.id, "asr", 1024, device_id="deskbot_bob")
    record_usage(row.id, "llm", 512, device_id="deskbot_bob")
    from deskbot_server.dao.api_key_service import get_user_usage_today

    summary = get_user_usage_summary(user.id, days=7)
    assert summary["totals"]["asr_bytes"] == 1024
    assert summary["totals"]["llm_bytes"] == 512
    assert len(summary["key_stats"]) == 1
    today = get_user_usage_today(user.id)
    assert today["asr_bytes"] == 1024
    assert today["llm_bytes"] == 512


def test_free_key_usage_visible_to_device_owner(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.dao.api_key_service import authenticate_api_key, get_user_usage_today, record_usage
    from tests.device_bind_helpers import bind_device_online

    user = create_user("dave@example.com", "password1234")
    bind_device_online(user.id, "deskbot_dave")

    raw = _read_free_key_from_file(temp_db.parent / ".free_api_key")
    auth = authenticate_api_key(raw)
    assert auth is not None
    record_usage(auth.api_key_id, "asr", 4096, device_id="deskbot_dave")
    record_usage(auth.api_key_id, "llm", 256, device_id="deskbot_dave")

    today = get_user_usage_today(user.id)
    assert today["asr_bytes"] == 4096
    assert today["llm_bytes"] == 256
    assert authenticate_api_key("invalid") is None


def test_device_level_usage(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.dao.api_key_service import create_api_key, get_user_device_usage_summary, record_usage
    from tests.device_bind_helpers import bind_device_online

    user = create_user("carol@example.com", "password1234")
    bind_device_online(user.id, "deskbot_dev1")
    raw, row = create_api_key(user.id, name="dev")
    record_usage(row.id, "asr", 2048, device_id="deskbot_dev1")
    record_usage(row.id, "face", 512, device_id="deskbot_dev1")

    dev_summary = get_user_device_usage_summary(user.id, days=7)
    assert dev_summary["totals"]["asr_bytes"] == 2048
    assert dev_summary["totals"]["face_bytes"] == 512
    assert len(dev_summary["device_stats"]) == 1
    assert dev_summary["device_stats"][0]["device_id"] == "deskbot_dev1"
    assert dev_summary["today_by_device"][0]["total_bytes"] == 2560


def test_free_key_quota_exceeded(temp_db):
    from deskbot_server.dao.api_key_service import (
        FREE_DAILY_QUOTA_BYTES,
        QuotaExceededError,
        authenticate_api_key,
        record_usage_checked,
    )

    raw = _read_free_key_from_file(temp_db.parent / ".free_api_key")
    auth = authenticate_api_key(raw)
    assert auth is not None

    record_usage_checked(auth.api_key_id, "asr", FREE_DAILY_QUOTA_BYTES - 100)
    with pytest.raises(QuotaExceededError):
        record_usage_checked(auth.api_key_id, "tts", 200)


def test_free_key_from_file_only(temp_db):
    from deskbot_server.dao.api_key_service import authenticate_api_key, write_free_api_key_file

    custom_key = "odk_free_customKeyForFileOnlyTest"
    write_free_api_key_file(custom_key)
    auth = authenticate_api_key(custom_key)
    assert auth is not None
    assert auth.is_free is True
    assert authenticate_api_key("odk_free_oldKeyNotInFile") is None


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
