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


def _minimal_design_doc() -> dict:
    return {"name": "test", "phonemes": [], "emotions": []}


@pytest.fixture()
def design_file(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        global_dir = root / "global"
        global_dir.mkdir()
        design_path = global_dir / "deskbot-face.json"
        design_path.write_text(
            json.dumps(_minimal_design_doc(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("deskbot_server.device_data.DATA_DIR", root)
        monkeypatch.setattr("deskbot_server.device_data.DEVICE_DATA_ROOT", root / "device")
        from deskbot_server.face_design_store import clear_face_design_cache

        clear_face_design_cache()
        yield design_path


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


def test_save_preserves_custom_default(design_file: Path):
    custom = _custom_default_scene()
    saved = save_face_expr_scenes_file([custom])
    assert saved[0]["frames"][0]["ms"] == 999

    reloaded = load_face_expr_scenes_file(seed_if_missing=False)
    assert reloaded is not None
    default_row = next(r for r in reloaded if r["name"] == "default")
    assert default_row["title"] == "我的默认眨眼"
    assert default_row["frames"][0]["ms"] == 999

    on_disk = json.loads(design_file.read_text(encoding="utf-8"))
    assert on_disk["emotions"][0]["frames"][0]["ms"] == 999


def test_save_preserves_custom_scene_and_reload(design_file: Path):
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
    assert len(rows) == 1
    mine = next(r for r in rows if r["name"] == "my_test_scene")
    assert mine["frames"][0]["elements"]["mouth"][0]["x"] == 10


def test_normalize_rejects_invalid_name():
    with pytest.raises(ValueError, match="invalid name"):
        normalize_face_expr_scenes([{"name": "Bad-Name", "frames": [{"ms": 500, "elements": {}}]}])
