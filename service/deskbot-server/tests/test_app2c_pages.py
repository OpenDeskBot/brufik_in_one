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


def _login_client():
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("u2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "u2c@example.com", "password": "password1234"})
    return client


PAGES = ["/home", "/voice", "/expr", "/my/memories", "/my/reminders", "/my/people", "/my/devices", "/advanced"]


def test_2c_pages_redirect_when_anonymous(temp_db):
    from deskbot_server.web.app import create_app

    client = create_app().test_client()
    assert client.get("/home").status_code == 302


@pytest.mark.parametrize("path", PAGES)
def test_2c_pages_render_when_logged_in(temp_db, path):
    resp = _login_client().get(path)
    assert resp.status_code == 200
