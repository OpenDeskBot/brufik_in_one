from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def temp_db(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("DESKBOT_DB_PATH", str(Path(tmp) / "t.db"))
        from deskbot_server.db import init_database
        from deskbot_server.db.engine import init_engine, reset_engine

        reset_engine()
        init_engine(Path(tmp) / "t.db")
        init_database()
        yield


def _client():
    from deskbot_server.auth.device_service import bind_device
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    user = create_user("map@example.com", "password1234")
    bind_device(user.id, "deskbot_map")
    app = create_app()
    c = app.test_client()
    c.post("/login", data={"email": "map@example.com", "password": "password1234"})
    c.post("/app/api/devices/select", json={"device_id": "deskbot_map"})
    return c


def test_get_empty_then_post_roundtrip(temp_db):
    c = _client()
    g = c.get("/api/emotion_expr_map").get_json()
    assert g["ok"] is True and g["map"] == {}

    p = c.post("/api/emotion_expr_map", json={"map": {"happy": "smile"}}).get_json()
    assert p["ok"] is True

    g2 = c.get("/api/emotion_expr_map").get_json()
    assert g2["map"] == {"happy": "smile"}
