from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def map_file(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "emotion_expr_map.json"
        monkeypatch.setattr(
            "deskbot_server.emotion_expr_map_store.EMOTION_EXPR_MAP_FILE",
            str(path),
        )
        yield path


def test_load_defaults_empty(map_file):
    from deskbot_server.emotion_expr_map_store import load_emotion_expr_map

    assert load_emotion_expr_map(device_id=None) == {}


def test_save_then_load_roundtrip(map_file):
    from deskbot_server.emotion_expr_map_store import (
        load_emotion_expr_map,
        save_emotion_expr_map,
    )

    save_emotion_expr_map({"happy": "smile", "sad": "sad"}, device_id=None)
    assert load_emotion_expr_map(device_id=None) == {"happy": "smile", "sad": "sad"}


def test_save_rejects_non_string_values(map_file):
    from deskbot_server.emotion_expr_map_store import save_emotion_expr_map

    with pytest.raises(ValueError):
        save_emotion_expr_map({"happy": 123}, device_id=None)
