from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def db_env(monkeypatch, tmp_path):
    """初始化内存 DB，返回 device_id。"""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DESKBOT_DB_PATH", str(db_path))
    monkeypatch.setattr("deskbot_server.utils.device_data.ensure_device_data_initialized", lambda _did: False)

    from deskbot_server.db import init_database
    from deskbot_server.db.engine import init_engine, reset_engine

    reset_engine()
    init_engine(db_path)
    init_database()
    return "test_device"


def test_delete_face_profile(db_env):
    from deskbot_server.service.face_profile_service import (
        delete_face_profile,
        list_face_profiles_summary,
        upsert_profile,
    )

    device_id = db_env
    p1 = upsert_profile(device_id, name="小明", descriptor=[0.1] * 512, merge_threshold=0.40)
    p2 = upsert_profile(device_id, name="小红", descriptor=[0.2] * 512, merge_threshold=0.40)
    assert len(list_face_profiles_summary(device_id=device_id)) == 2
    assert delete_face_profile(p1["id"], device_id=device_id)
    rows = list_face_profiles_summary(device_id=device_id)
    assert len(rows) == 1
    assert rows[0]["id"] == p2["id"]
    assert rows[0]["name"] == "小红"
    assert "descriptor" not in rows[0]


def test_update_face_profile_name(db_env):
    from deskbot_server.service.face_profile_service import (
        list_face_profiles_summary,
        update_face_profile_name,
        upsert_profile,
    )

    device_id = db_env
    p = upsert_profile(device_id, name="旧名字", descriptor=[0.1] * 512, merge_threshold=0.40)
    updated = update_face_profile_name(p["id"], "新名字", device_id=device_id)
    assert updated is not None
    assert updated["name"] == "新名字"
    rows = list_face_profiles_summary(device_id=device_id)
    assert rows[0]["name"] == "新名字"
    assert update_face_profile_name(9999, "不存在", device_id=device_id) is None


def test_update_face_profile_api(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DESKBOT_DB_PATH", str(db_path))
    monkeypatch.setattr("deskbot_server.utils.device_data.ensure_device_data_initialized", lambda _did: False)

    from deskbot_server.service.face_profile_service import upsert_profile
    from deskbot_server.db import init_database
    from deskbot_server.db.engine import init_engine, reset_engine
    from deskbot_server.web.app import create_app

    reset_engine()
    init_engine(db_path)
    init_database()

    from tests.device_bind_helpers import bind_device_online

    user = __import__("deskbot_server.auth.service", fromlist=["create_user"]).create_user(
        "face-api@example.com", "password1234"
    )
    bind_device_online(user.id, "deskbot_face")
    p = upsert_profile("deskbot_face", name="旧名字", descriptor=[0.1] * 512, merge_threshold=0.40)

    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "face-api@example.com", "password": "password1234"})
    client.post("/app/api/devices/select", json={"device_id": "deskbot_face"})
    resp = client.put(f"/app/api/face-profiles/{p['id']}", json={"name": "新名字"})
    assert resp.status_code == 200
    assert resp.get_json()["profile"]["name"] == "新名字"
