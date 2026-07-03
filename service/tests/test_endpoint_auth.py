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


def test_flask_api_requires_session(temp_db):
    from deskbot_server.web.app import create_app

    app = create_app()
    client = app.test_client()

    assert client.get("/health").status_code == 200
    assert client.get("/debug/devices").status_code == 302


def test_flask_api_allows_logged_in_developer(temp_db):
    from deskbot_server.auth.service import create_user, set_user_developer
    from deskbot_server.web.app import create_app

    user = create_user("alice@example.com", "password1234")
    set_user_developer(user.id, is_developer=True)
    app = create_app()
    client = app.test_client()

    login = client.post(
        "/login",
        data={"email": "alice@example.com", "password": "password1234"},
        follow_redirects=False,
    )
    assert login.status_code == 302

    resp = client.get("/debug/llm")
    assert resp.status_code == 200


def test_flask_api_denies_debug_for_non_developer(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("first@example.com", "password1234")
    create_user("bob@example.com", "password1234")
    app = create_app()
    client = app.test_client()

    login = client.post(
        "/login",
        data={"email": "bob@example.com", "password": "password1234"},
        follow_redirects=False,
    )
    assert login.status_code == 302

    resp = client.get("/debug/llm")
    assert resp.status_code == 302
    assert "/app/" in resp.headers.get("Location", "")


def test_register_and_login_flow(temp_db):
    from deskbot_server.web.app import create_app

    app = create_app()
    client = app.test_client()

    r = client.post(
        "/register",
        data={
            "email": "newbie@example.com",
            "password": "password1234",
            "confirm_password": "password1234",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302

    client.post("/logout")
    r2 = client.post(
        "/login",
        data={"email": "newbie@example.com", "password": "password1234"},
        follow_redirects=False,
    )
    assert r2.status_code == 302


def test_developer_user_management(temp_db):
    from deskbot_server.auth.service import create_user, get_user_by_email, set_user_developer
    from deskbot_server.web.app import create_app

    admin = create_user("admin@example.com", "password1234")
    set_user_developer(admin.id, is_developer=True)
    create_user("member@example.com", "password1234")

    app = create_app()
    client = app.test_client()
    client.post(
        "/login",
        data={"email": "admin@example.com", "password": "password1234"},
    )

    resp = client.get("/debug/users")
    assert resp.status_code == 200
    assert b"member@example.com" in resp.data

    member = get_user_by_email("member@example.com")
    assert member is not None

    api = client.post(
        f"/api/debug/users/{member.id}/developer",
        json={"is_developer": True},
    )
    assert api.status_code == 200
    assert api.get_json()["user"]["is_developer"] is True


def test_http_require_api_key_rejects_missing():
    from deskbot_server.ws.api_key_gate import http_require_api_key

    with pytest.raises(PermissionError, match="api_key_required"):
        http_require_api_key({}, {})


def test_http_require_api_key_accepts_valid_key(temp_db):
    from deskbot_server.auth.api_key_service import authenticate_api_key, create_api_key
    from deskbot_server.auth.service import create_user
    from deskbot_server.ws.api_key_gate import http_require_api_key

    user = create_user("bob@example.com", "password1234")
    raw, _row = create_api_key(user.id, name="dev")
    auth = http_require_api_key({"api_key": raw}, {})
    assert auth is not None
    assert authenticate_api_key(raw) is not None
    assert auth.user_id == user.id


def test_http_require_device_access_enforces_ownership(temp_db):
    from deskbot_server.auth.api_key_service import authenticate_api_key, create_api_key
    from deskbot_server.auth.device_service import bind_device
    from deskbot_server.auth.service import create_user
    from deskbot_server.ws.api_key_gate import http_require_device_access

    user = create_user("carol@example.com", "password1234")
    bind_device(user.id, "deskbot_a")
    raw, _row = create_api_key(user.id, name="dev")
    auth = authenticate_api_key(raw)
    assert auth is not None

    http_require_device_access(auth, "deskbot_a")
    with pytest.raises(PermissionError, match="forbidden_device"):
        http_require_device_access(auth, "deskbot_b")


def test_http_require_device_access_skips_free_key(temp_db):
    from deskbot_server.auth.api_key_service import authenticate_api_key
    from deskbot_server.ws.api_key_gate import http_require_device_access

    key_file = temp_db.parent / ".free_api_key"
    raw = None
    for line in key_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("api_key="):
            raw = line.split("=", 1)[1].strip()
            break
    assert raw
    auth = authenticate_api_key(raw)
    assert auth is not None
    assert auth.user_id is None

    http_require_device_access(auth, "any_device_id")
