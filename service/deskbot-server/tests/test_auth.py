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
        from deskbot_server.db.engine import init_engine, reset_engine
        from deskbot_server.db import init_database

        reset_engine()
        init_engine(db_path)
        init_database()
        yield db_path


def test_free_api_key_seed(temp_db):
    from deskbot_server.auth.api_key_service import (
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
    from deskbot_server.auth.device_service import bind_device, user_owns_device
    from deskbot_server.auth.service import create_user

    user = create_user("alice@example.com", "secret1234")
    device = bind_device(user.id, "deskbot_a1")
    assert device.device_id == "deskbot_a1"
    assert user_owns_device(user.id, "deskbot_a1")


def test_bind_conflict(temp_db):
    from deskbot_server.auth.device_service import bind_device
    from deskbot_server.auth.service import create_user

    u1 = create_user("u1@example.com", "password123")
    u2 = create_user("u2@example.com", "password456")
    bind_device(u1.id, "deskbot_shared")
    with pytest.raises(ValueError, match="其他账号"):
        bind_device(u2.id, "deskbot_shared")


def test_api_key_create_auth_and_usage(temp_db):
    from deskbot_server.auth.api_key_service import (
        authenticate_api_key,
        create_api_key,
        get_user_usage_summary,
        record_usage,
    )
    from deskbot_server.auth.device_service import bind_device
    from deskbot_server.auth.service import create_user, update_display_name

    user = create_user("bob@example.com", "password1234")
    update_display_name(user.id, "Bob")
    bind_device(user.id, "deskbot_bob")
    raw, row = create_api_key(user.id, name="dev")
    auth = authenticate_api_key(raw)
    assert auth is not None
    assert auth.user_id == user.id
    assert auth.is_free is False

    record_usage(row.id, "asr", 1024, device_id="deskbot_bob")
    record_usage(row.id, "llm", 512, device_id="deskbot_bob")
    from deskbot_server.auth.api_key_service import get_user_usage_today

    summary = get_user_usage_summary(user.id, days=7)
    assert summary["totals"]["asr_bytes"] == 1024
    assert summary["totals"]["llm_bytes"] == 512
    assert len(summary["key_stats"]) == 1
    today = get_user_usage_today(user.id)
    assert today["asr_bytes"] == 1024
    assert today["llm_bytes"] == 512


def test_free_key_usage_visible_to_device_owner(temp_db):
    from deskbot_server.auth.api_key_service import (
        authenticate_api_key,
        get_user_usage_today,
        record_usage,
    )
    from deskbot_server.auth.device_service import bind_device
    from deskbot_server.auth.service import create_user

    user = create_user("dave@example.com", "password1234")
    bind_device(user.id, "deskbot_dave")

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
    from deskbot_server.auth.api_key_service import create_api_key, get_user_device_usage_summary, record_usage
    from deskbot_server.auth.device_service import bind_device
    from deskbot_server.auth.service import create_user

    user = create_user("carol@example.com", "password1234")
    bind_device(user.id, "deskbot_dev1")
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
    from deskbot_server.auth.api_key_service import (
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
    from deskbot_server.auth.api_key_service import authenticate_api_key, write_free_api_key_file

    custom_key = "odk_free_customKeyForFileOnlyTest"
    write_free_api_key_file(custom_key)
    auth = authenticate_api_key(custom_key)
    assert auth is not None
    assert auth.is_free is True
    assert authenticate_api_key("odk_free_oldKeyNotInFile") is None
