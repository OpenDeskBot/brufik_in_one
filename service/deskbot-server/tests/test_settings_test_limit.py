from __future__ import annotations

import pytest


@pytest.fixture()
def temp_db(monkeypatch):
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        monkeypatch.setenv("DESKBOT_DB_PATH", str(db_path))
        from deskbot_server.db.engine import init_engine, reset_engine
        from deskbot_server.db import init_database

        reset_engine()
        init_engine(db_path)
        init_database()
        yield db_path


def test_settings_test_limit_per_user_and_ip(temp_db):
    from deskbot_server.application.settings_test_limit import (
        SETTINGS_TEST_DAILY_LIMIT,
        SettingsTestLimitExceeded,
        check_and_consume_settings_test,
        get_settings_test_quota,
    )

    user_a = "user-a"
    user_b = "user-b"
    ip_1 = "203.0.113.10"
    ip_2 = "203.0.113.11"

    for i in range(SETTINGS_TEST_DAILY_LIMIT):
        snap = check_and_consume_settings_test(user_id=user_a, client_ip=ip_1)
        assert snap.user_remaining == SETTINGS_TEST_DAILY_LIMIT - (i + 1)

    with pytest.raises(SettingsTestLimitExceeded) as exc:
        check_and_consume_settings_test(user_id=user_a, client_ip=ip_2)
    assert exc.value.scope == "user"

    with pytest.raises(SettingsTestLimitExceeded) as exc_ip:
        check_and_consume_settings_test(user_id=user_b, client_ip=ip_1)
    assert exc_ip.value.scope == "ip"

    snap_b = check_and_consume_settings_test(user_id=user_b, client_ip=ip_2)
    assert snap_b.user_remaining == SETTINGS_TEST_DAILY_LIMIT - 1

    quota = get_settings_test_quota(user_id=user_a, client_ip=ip_1)
    assert quota.user_count == SETTINGS_TEST_DAILY_LIMIT
    assert quota.ip_count == SETTINGS_TEST_DAILY_LIMIT
    assert quota.user_remaining == 0


def test_api_test_llm_returns_429_when_quota_exhausted(temp_db, monkeypatch):
    from deskbot_server.application.settings_test_limit import SETTINGS_TEST_DAILY_LIMIT
    from deskbot_server.auth.device_service import bind_device
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    user = create_user("quota@example.com", "password1234")
    bind_device(user.id, "deskbot_quota")

    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "quota@example.com", "password": "password1234"})

    def fake_completion(*_args, **_kwargs):
        return "ok", {"model": "test", "display_name": "test", "usage": {}}

    monkeypatch.setattr(
        "deskbot_server.web.blueprints.app_bp.litellm_completion",
        fake_completion,
    )

    url = "/app/api/llm-models/test?device_id=deskbot_quota"
    body = {
        "model_name": "qwen-flash",
        "protocol": "openai",
        "api_key": "sk-test",
        "prompt": "hi",
    }
    for _ in range(SETTINGS_TEST_DAILY_LIMIT):
        resp = client.post(url, json=body)
        assert resp.status_code == 200

    blocked = client.post(url, json=body)
    assert blocked.status_code == 429
    data = blocked.get_json()
    assert data["ok"] is False
    assert "上限" in data["error"]
