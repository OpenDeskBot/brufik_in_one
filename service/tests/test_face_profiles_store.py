from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def face_profiles_file(monkeypatch, tmp_path):
    path = tmp_path / "face_profiles.json"
    monkeypatch.setattr(
        "deskbot_server.face_profiles_store.resolve_json_path",
        lambda _default, device_id=None: str(path if not device_id else tmp_path / device_id / "face_profiles.json"),
    )
    return path


def test_delete_face_profile(face_profiles_file):
    from deskbot_server.face_profiles_store import (
        delete_face_profile,
        list_face_profiles_summary,
        save_face_profiles,
    )

    save_face_profiles(
        [
            {
                "person_id": 1,
                "name": "小明",
                "descriptor": [0.1] * 512,
                "descriptor_kind": "embedding",
            },
            {
                "person_id": 2,
                "name": "小红",
                "descriptor": [0.2] * 512,
                "descriptor_kind": "embedding",
            },
        ]
    )
    assert len(list_face_profiles_summary()) == 2
    assert delete_face_profile(1)
    rows = list_face_profiles_summary()
    assert len(rows) == 1
    assert rows[0]["person_id"] == 2
    assert rows[0]["name"] == "小红"
    assert "descriptor" not in rows[0]


def test_update_face_profile_name(face_profiles_file):
    from deskbot_server.face_profiles_store import (
        list_face_profiles_summary,
        save_face_profiles,
        update_face_profile_name,
    )

    save_face_profiles(
        [
            {
                "person_id": 1,
                "name": "旧名字",
                "descriptor": [0.1] * 512,
                "descriptor_kind": "embedding",
            }
        ]
    )
    updated = update_face_profile_name(1, "新名字")
    assert updated is not None
    assert updated["name"] == "新名字"
    rows = list_face_profiles_summary()
    assert rows[0]["name"] == "新名字"
    assert update_face_profile_name(404, "不存在") is None


def test_update_face_profile_api(monkeypatch, tmp_path):
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        monkeypatch.setenv("DESKBOT_DB_PATH", str(db_path))
        monkeypatch.setattr(
            "deskbot_server.device_data.ensure_device_data_initialized",
            lambda _device_id: False,
        )
        monkeypatch.setattr(
            "deskbot_server.face_profiles_store.resolve_json_path",
            lambda _default, device_id=None: str(
                tmp_path / (device_id or "global") / "face_profiles.json"
            ),
        )

        from deskbot_server.auth.device_service import bind_device
        from deskbot_server.auth.service import create_user
        from deskbot_server.db import init_database
        from deskbot_server.db.engine import init_engine, reset_engine
        from deskbot_server.face_profiles_store import save_face_profiles
        from deskbot_server.web.app import create_app

        reset_engine()
        init_engine(db_path)
        init_database()
        user = create_user("face-api@example.com", "password1234")
        bind_device(user.id, "deskbot_face")
        save_face_profiles(
            [
                {
                    "person_id": 1,
                    "name": "旧名字",
                    "descriptor": [0.1] * 512,
                    "descriptor_kind": "embedding",
                }
            ],
            device_id="deskbot_face",
        )

        app = create_app()
        client = app.test_client()
        client.post("/login", data={"email": "face-api@example.com", "password": "password1234"})
        client.post("/app/api/devices/select", json={"device_id": "deskbot_face"})
        resp = client.put("/app/api/face-profiles/1", json={"name": "新名字"})
        assert resp.status_code == 200
        assert resp.get_json()["profile"]["name"] == "新名字"
