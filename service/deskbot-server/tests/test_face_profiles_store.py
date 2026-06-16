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
