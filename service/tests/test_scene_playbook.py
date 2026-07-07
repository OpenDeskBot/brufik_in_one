"""场景编排 store / runner 单元测试。"""
from __future__ import annotations

from deskbot_server.scene_playbook_runner import (
    playbook_collect_text,
    playbook_to_llm_plan,
    playbook_to_phases,
)
from deskbot_server.scene_playbooks_store import (
    find_playbook_by_name,
    normalize_playbook,
    normalize_scene_playbooks,
)


def test_playbook_to_llm_plan_demo_greet():
    pb = normalize_playbook(
        {
            "name": "demo_greet",
            "title": "演示问候",
            "chunks": [
                {"id": "c1", "text": "", "servo": {"preset": "center", "ms": 500}},
                {"id": "c2", "text": "你好", "expr": {"scene": "happy_smile", "ms": 800}},
                {"id": "c3", "text": "世界", "expr": {"scene": "default", "ms": 500}},
            ],
        }
    )
    text, moves, anims, leading = playbook_to_llm_plan(pb)
    assert text == "你好世界"
    assert moves == [{"move": "center", "ms": 500}]
    assert anims == [
        {"anim": "happy_smile", "ms": 800},
        {"anim": "default", "ms": 500},
    ]
    assert leading == 0


def test_playbook_chunks_to_phases():
    pb = normalize_playbook(
        {
            "name": "look_then_speak",
            "title": "先看再说",
            "chunks": [
                {"id": "s1", "text": "", "servo": {"preset": "look_left", "ms": 500}},
                {"id": "s2", "text": "", "servo": {"preset": "look_right", "ms": 500}},
                {"id": "e1", "text": "", "expr": {"scene": "surprised", "ms": 800}},
                {
                    "id": "t1",
                    "text": "你好",
                    "expr": {"scene": "happy", "ms": 2000},
                },
            ],
        }
    )
    phases = playbook_to_phases(pb)
    assert len(phases) == 4
    assert phases[0]["kind"] == "motion" and phases[0]["moves"]
    assert phases[1]["kind"] == "motion"
    assert phases[2]["kind"] == "motion" and phases[2]["anims"] == [
        {"anim": "surprised", "ms": 800}
    ]
    assert phases[3]["kind"] == "speech"
    assert phases[3]["text"] == "你好"
    assert phases[3]["anims"] == [{"anim": "happy", "ms": 2000}]


def test_combined_chunk_speech_servo_expr():
    pb = normalize_playbook(
        {
            "name": "combo",
            "chunks": [
                {
                    "id": "c1",
                    "text": "嗨",
                    "servo": {"preset": "center", "ms": 300},
                    "expr": {"scene": "happy", "ms": 1000},
                },
            ],
        }
    )
    phases = playbook_to_phases(pb)
    assert len(phases) == 1
    assert phases[0]["kind"] == "speech"
    assert phases[0]["text"] == "嗨"
    assert phases[0]["moves"] == [{"move": "center", "ms": 300}]
    assert phases[0]["anims"] == [{"anim": "happy", "ms": 1000}]


def test_legacy_format_migrated_to_chunks():
    pb = normalize_playbook(
        {
            "name": "legacy",
            "text": "你好",
            "speech_start_ms": 3000,
            "servo_track": [{"id": "s1", "preset": "look_left", "ms": 500}],
            "expr_track": [{"id": "e1", "scene": "happy", "ms": 800}],
        }
    )
    assert len(pb["chunks"]) >= 2
    assert playbook_collect_text(pb) == "你好"


def test_playbook_collect_text():
    pb = normalize_playbook(
        {
            "name": "say_hi",
            "title": "hi",
            "chunks": [
                {"id": "c1", "text": "你好"},
                {"id": "c2", "text": "呀"},
            ],
        }
    )
    assert playbook_collect_text(pb) == "你好呀"


def test_collect_missing_servo_presets(tmp_path, monkeypatch):
    from deskbot_server import scene_playbooks_store as sps
    from deskbot_server.constants import SERVO_CFG_FILE

    servo_path = tmp_path / "servo.json"
    servo_path.write_text(
        '{"xMin":0,"xMax":180,"yMin":70,"yMax":110,"xReverse":0,"yReverse":0,'
        '"perspective":"viewer","presets":[{"id":"shake_head","label":"摇头",'
        '"steps":[{"x":-18,"y":0,"xm":1,"ym":1,"ms":400}]}]}',
        encoding="utf-8",
    )

    def _resolve(path, device_id=None):
        if path and "servo" in str(path):
            return str(servo_path)
        return str(tmp_path / str(path or "x"))

    monkeypatch.setattr("deskbot_server.device_data.resolve_json_path", _resolve)
    monkeypatch.setattr("deskbot_server.servo_config_store.resolve_json_path", _resolve)

    pb = {
        "name": "dance",
        "chunks": [
            {"id": "c1", "text": "", "servo": {"preset": "shake_head", "ms": 500}},
            {"id": "c2", "text": "", "servo": {"preset": "preset_custom", "ms": 500}},
        ],
    }
    missing = sps.collect_missing_servo_presets(pb, device_id="dev1")
    assert missing == ["preset_custom"]


def test_find_playbook_by_name_case_insensitive():
    rows = normalize_scene_playbooks(
        [
            {
                "name": "nod",
                "title": "点头",
                "chunks": [{"id": "c1", "text": "", "servo": {"preset": "center", "ms": 500}}],
            }
        ]
    )
    found = find_playbook_by_name(rows, "NOD")
    assert found is not None
    assert found["name"] == "nod"
