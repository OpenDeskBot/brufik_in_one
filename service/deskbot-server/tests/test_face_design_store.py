"""face_design_store 单元测试。"""
from __future__ import annotations

import json

from deskbot_server.face_design_store import (
    find_emotion_expression,
    find_phoneme_expression,
    normalize_face_design_doc,
    pick_expression_elements,
)


def test_normalize_legacy_field_names():
    doc = normalize_face_design_doc(
        {
            "name": "test",
            "phoneme_expressions": [
                {
                    "name": "a",
                    "alias": ["AA"],
                    "title": "a",
                    "frames": [{"ms": 100, "elements": {"mouth": [], "nose": [], "eye_l": [], "eye_r": [], "extra": []}}],
                }
            ],
            "emotion_expressions": [
                {
                    "name": "idle",
                    "alias": ["default"],
                    "title": "idle",
                    "frames": [{"ms": 100, "elements": {"mouth": [], "nose": [], "eye_l": [], "eye_r": [], "extra": []}}],
                }
            ],
        }
    )
    assert doc["phonemes"][0]["name"] == "a"
    assert find_phoneme_expression(doc, "AA") is not None
    assert find_emotion_expression(doc, "default") is not None


def test_pick_expression_elements_multi_frame():
    doc = normalize_face_design_doc(
        {
            "phonemes": [
                {
                    "name": "ai",
                    "frames": [
                        {"ms": 400, "elements": {"mouth": [{"shape": "rect", "x": 1, "y": 2, "w": 3, "h": 4}]}},
                        {"ms": 400, "elements": {"mouth": [{"shape": "rect", "x": 9, "y": 9, "w": 9, "h": 9}]}},
                    ],
                }
            ],
            "emotions": [],
        }
    )
    expr = doc["phonemes"][0]
    assert pick_expression_elements(expr, at_ms=0)["mouth"][0]["x"] == 1
    assert pick_expression_elements(expr, at_ms=450)["mouth"][0]["x"] == 9


def test_save_emotions_to_design_file(tmp_path, monkeypatch):
    design_path = tmp_path / "deskbot-face.json"
    design_path.write_text(
        json.dumps(
            {
                "name": "test",
                "phonemes": [],
                "emotions": [
                    {
                        "name": "idle",
                        "alias": ["default"],
                        "title": "idle",
                        "frames": [{"ms": 100, "elements": {"mouth": [], "nose": [], "eye_l": [], "eye_r": [], "extra": []}}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("deskbot_server.face_design_store.FACE_DESIGN_FILE", str(design_path))
    from deskbot_server.face_design_store import clear_face_design_cache
    from deskbot_server.face_expr_scenes_store import load_face_expr_scenes_file, save_face_expr_scenes_file

    clear_face_design_cache()
    rows = save_face_expr_scenes_file(
        [
            {
                "name": "happy",
                "title": "开心",
                "frames": [{"ms": 200, "elements": {"mouth": [], "nose": [], "eye_l": [], "eye_r": [], "extra": []}}],
            }
        ]
    )
    assert len(rows) == 1
    assert rows[0]["name"] == "happy"
    reloaded = load_face_expr_scenes_file(seed_if_missing=False)
    assert reloaded is not None
    assert {r["name"] for r in reloaded} == {"happy"}


def test_resolve_face_design_path_uses_global(tmp_path, monkeypatch):
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    global_path = global_dir / "deskbot-face.json"
    global_path.write_text('{"name":"g","phonemes":[],"emotions":[]}', encoding="utf-8")
    device_dir = tmp_path / "device" / "dev1"
    device_dir.mkdir(parents=True)
    (device_dir / "deskbot-face.json").write_text('{"name":"d","phonemes":[],"emotions":[]}', encoding="utf-8")
    monkeypatch.setattr("deskbot_server.face_design_store.FACE_DESIGN_FILE", str(global_path))
    monkeypatch.setattr("deskbot_server.device_data.DATA_DIR", tmp_path)
    from deskbot_server.face_design_store import resolve_face_design_path

    assert resolve_face_design_path(device_id="dev1") == str(global_path)
