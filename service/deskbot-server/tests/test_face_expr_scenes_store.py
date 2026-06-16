from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from deskbot_server.face_expr_scenes_store import (
    load_face_expr_scenes_file,
    normalize_face_expr_scenes,
    save_face_expr_scenes_file,
)


@pytest.fixture()
def scenes_file(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "face_expr_scenes.json"
        monkeypatch.setattr(
            "deskbot_server.face_expr_scenes_store.FACE_EXPR_SCENES_FILE",
            str(path),
        )
        yield path


def _custom_default_scene() -> dict:
    return {
        "name": "default",
        "title": "我的默认眨眼",
        "frames": [
            {
                "ms": 999,
                "elements": {
                    "mouth": [],
                    "nose": [{"shape": "circle", "x": 1, "y": 2, "r": 3}],
                    "eye_l": [{"shape": "ellipse_fill", "x": 4, "y": 5, "rw": 6, "rh": 7}],
                    "eye_r": [{"shape": "ellipse_fill", "x": 8, "y": 9, "rw": 10, "rh": 11}],
                    "extra": [],
                },
            }
        ],
    }


def test_save_preserves_custom_default(scenes_file: Path):
    custom = _custom_default_scene()
    saved = save_face_expr_scenes_file([custom])
    assert saved[0]["frames"][0]["ms"] == 999

    reloaded = load_face_expr_scenes_file(seed_if_missing=False)
    assert reloaded is not None
    default_row = next(r for r in reloaded if r["name"] == "default")
    assert default_row["title"] == "我的默认眨眼"
    assert default_row["frames"][0]["ms"] == 999

    on_disk = json.loads(scenes_file.read_text(encoding="utf-8"))
    assert on_disk[0]["frames"][0]["ms"] == 999


def test_save_preserves_custom_scene_and_reload(scenes_file: Path):
    scene = {
        "name": "my_test_scene",
        "title": "测试",
        "frames": [
            {
                "ms": 500,
                "elements": {
                    "mouth": [{"shape": "circle", "x": 10, "y": 20, "r": 3}],
                    "nose": [],
                    "eye_l": [],
                    "eye_r": [],
                    "extra": [],
                },
            }
        ],
    }
    save_face_expr_scenes_file([scene])
    rows = load_face_expr_scenes_file(seed_if_missing=False)
    assert len(rows) == 2  # my_test_scene + auto default
    mine = next(r for r in rows if r["name"] == "my_test_scene")
    assert mine["frames"][0]["elements"]["mouth"][0]["x"] == 10


def test_normalize_rejects_invalid_name():
    with pytest.raises(ValueError, match="invalid name"):
        normalize_face_expr_scenes([{"name": "Bad-Name", "frames": [{"ms": 500, "elements": {}}]}])
