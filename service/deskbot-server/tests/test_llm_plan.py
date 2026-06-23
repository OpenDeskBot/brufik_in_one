from __future__ import annotations

import json

from deskbot_server.llm.utils import parse_llm_reply
from deskbot_server.pb.llm_plan import (
    expand_llm_anims,
    expand_llm_moves,
    interleave_tts_segs_with_llm_plan,
    map_anim_frames_to_tts_segs,
    merge_llm_plan_anim_rows,
)


def test_parse_llm_reply_tool_only_array():
    raw = '[{"tool":"set_camera_follow","mode":"follow"}]'
    parsed = parse_llm_reply(raw)
    assert parsed["json_ok"] is True
    assert parsed["tools"] == [{"tool": "set_camera_follow", "mode": "follow"}]
    assert parsed["reply"] == ""


def test_parse_llm_reply_moves_anims():
    raw = (
        '{"need_reply": true, "tts": "你好", '
        '"moves": [{"move": "nod_head", "ms": 540}], '
        '"anims": [{"anim": "default", "ms": 1500}]}'
    )
    parsed = parse_llm_reply(raw)
    assert parsed["json_ok"] is True
    assert parsed["reply"] == "你好"
    assert parsed["moves"] == [{"move": "nod_head", "ms": 540}]
    assert parsed["anims"] == [{"anim": "default", "ms": 1500}]


def test_expand_llm_moves_scales_preset_steps():
    steps = expand_llm_moves([{"move": "nod_head", "ms": 1080}])
    assert len(steps) == 3
    assert sum(s["ms"] for s in steps) == 1080


def test_expand_llm_anims_bg_color():
    frames = expand_llm_anims(
        [{"anim": "default", "ms": 200, "bg": "#000000", "color": "yellow"}]
    )
    assert frames
    bg = frames[0]["elements"].get("bg") or []
    assert bg and bg[0]["shape"] == "rect"
    assert bg[0].get("color") == "#000000"


def test_expand_llm_anims_fallback_default():
    frames = expand_llm_anims([{"anim": "__no_such_anim__", "ms": 800}])
    assert frames
    assert sum(f["ms"] for f in frames) == 800
    assert isinstance(frames[0].get("elements"), dict)


def test_interleave_tts_with_llm_plan_parallel():
    segs = [{"phoneme": "n", "ms": 100, "pcm": b"\x00" * 4800}]
    move_steps = [{"xm": 1, "ym": 1, "x": 0, "y": 10, "ms": 200}]
    anim_frames = [{"ms": 150, "elements": {"mouth": [], "eye_l": [], "eye_r": [], "nose": [], "extra": []}}]
    out, servo, anim = interleave_tts_segs_with_llm_plan(segs, move_steps, anim_frames, 24000)
    assert len(out) == 1
    assert out[0]["ms"] == 100
    assert servo[0]["ms"] == 200
    assert anim[0] is not None


def test_map_anim_frames_covers_all_tts_segs_not_index_only():
    """多音素分片时，anims 应按时间轴覆盖，而非只贴前 N 帧。"""
    segs = [{"phoneme": "a", "ms": 200}, {"phoneme": "b", "ms": 200}, {"phoneme": "c", "ms": 200}]
    anim_frames = [
        {"ms": 300, "elements": {"extra": [{"shape": "circle", "x": 1, "y": 2, "r": 3}]}},
        {"ms": 300, "elements": {"extra": [{"shape": "circle", "x": 9, "y": 9, "r": 9}]}},
    ]
    parallel = map_anim_frames_to_tts_segs(segs, anim_frames)
    assert len(parallel) == 3
    assert all(p is not None for p in parallel)
    assert parallel[0]["extra"][0]["x"] == 1
    assert parallel[2]["extra"][0]["x"] == 9


def test_merge_llm_plan_anim_rows_keeps_phoneme_mouth():
    segs = [{"phoneme": "a", "ms": 100, "pcm": b"\x00" * 4800}]
    phoneme_rows = [
        {
            "idx": 0,
            "chunk_ms": 100,
            "anim": [
                {
                    "elements": {
                        "mouth": [{"shape": "rect", "x": 1, "y": 2, "w": 3, "h": 4}],
                        "eye_l": [],
                        "eye_r": [],
                        "nose": [],
                        "extra": [],
                    },
                    "ms": 100,
                    "phoneme": "a",
                }
            ],
        }
    ]
    plan_el = {
        "mouth": [{"shape": "line", "x1": 0, "y1": 0, "x2": 1, "y2": 1}],
        "eye_l": [{"shape": "circle", "x": 1, "y": 2, "r": 3}],
        "eye_r": [],
        "nose": [],
        "extra": [],
    }
    merged = merge_llm_plan_anim_rows(segs, phoneme_rows, [plan_el])
    mouth = merged[0]["anim"][0]["elements"]["mouth"]
    assert mouth == phoneme_rows[0]["anim"][0]["elements"]["mouth"]
    assert merged[0]["anim"][0]["elements"]["eye_l"] == plan_el["eye_l"]


def test_llm_face_context_prompt_appendix():
    from deskbot_server.llm.utils import llm_static_context_prompt_appendix

    text = llm_static_context_prompt_appendix("test_device_faces_prompt")
    assert "register_face" in text
    assert "长期记忆" in text
    assert "face_id=" not in text


def test_build_llm_user_message():
    from deskbot_server.face_snapshot_cache import update_device_faces
    from deskbot_server.llm.user_message import build_llm_user_message

    dev = "test_device_user_msg"
    update_device_faces(
        dev,
        [
            {
                "face_id": 1,
                "person_name": "小明",
                "identity_score": 0.82,
                "face_score": 0.95,
                "person_id": 1,
                "image_w": 320,
                "image_h": 240,
                "landmarks": [{"name": "nose", "x": 200, "y": 140}],
                "points": [],
            },
        ],
    )
    ack = '{"type":"pb_ack","servo":{"x":90,"y":75}}'
    msg = build_llm_user_message("你好", device_id=dev, device_context=ack)
    assert "水平舵机角度: 90" in msg
    assert "垂直舵机角度: 75" in msg
    assert "faceid=1" in msg
    assert "name=小明" in msg
    assert "脸中心位置=(200,140)" in msg
    assert "用户正文: 你好" in msg

    silent = build_llm_user_message("", device_id=dev, device_context=ack)
    assert "用户正文: [未说话]" in silent


def test_parse_llm_tools():
    raw = '{"tts":"好","tools":[{"tool":"memory_add","text":"喜欢猫"}]}'
    parsed = parse_llm_reply(raw)
    assert parsed["tools"] == [{"tool": "memory_add", "text": "喜欢猫"}]


def test_parse_llm_reply_volume_cam_fps():
    raw = '{"tts":"好","volume":75,"cam_fps":4,"moves":[],"anims":[]}'
    parsed = parse_llm_reply(raw)
    assert parsed["volume"] == 75
    assert parsed["cam_fps"] == 4


def test_resize_jpeg_for_lcd_display():
    import base64
    import io

    from PIL import Image

    from deskbot_server.pb.llm_display import decode_llm_image_item, jpeg_blob_dimensions

    buf = io.BytesIO()
    Image.new("RGB", (320, 240), color=(40, 120, 200)).save(buf, format="JPEG")
    b64 = base64.standard_b64encode(buf.getvalue()).decode("ascii")
    dec = decode_llm_image_item({"b64": b64, "x": 0, "y": 0, "w": 284, "h": 240})
    assert dec is not None
    assert dec["w"] == 284 and dec["h"] == 240
    assert jpeg_blob_dimensions(dec["bytes"]) == (284, 240)


def test_parse_llm_reply_screen_and_images():
    import base64

    b64 = base64.standard_b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 8).decode()
    raw = json.dumps(
        {
            "tts": "看",
            "screen_text": "第一行\n第二行",
            "screen_text_color": "#FFF",
            "images": [{"b64": b64, "x": 0, "y": 0, "w": 100, "h": 80}],
        },
        ensure_ascii=False,
    )
    parsed = parse_llm_reply(raw)
    assert parsed["json_ok"] is True
    assert parsed["screen_text"] == "第一行\n第二行"
    assert parsed["screen_text_color"] == "#FFF"
    assert len(parsed["images"]) == 1


def test_device_volume_persist(tmp_path, monkeypatch):
    from deskbot_server import device_volume_store as dvs

    vol_file = tmp_path / "device_volume.json"

    def _resolve(path, device_id=None):
        return str(vol_file)

    monkeypatch.setattr(dvs, "resolve_json_path", _resolve)
    monkeypatch.setattr(dvs, "DEVICE_VOLUME_FILE", str(vol_file))
    assert dvs.persist_device_volume(55, device_id="dev1") == 55
    assert dvs.get_device_volume("dev1") == 55
    assert dvs.persist_device_volume(90, device_id="dev1") == 90
    assert dvs.get_device_volume("dev1") == 90
    raw = '{"tts":"好","volume":75,"cam_fps":4,"moves":[],"anims":[]}'
    parsed = parse_llm_reply(raw)
    assert parsed["volume"] == 75
    assert parsed["cam_fps"] == 4
    omit = parse_llm_reply('{"tts":"好","moves":[],"anims":[]}')
    assert omit["volume"] is None
    assert omit["cam_fps"] is None


def test_parse_llm_reply_empty_tts_not_raw_json():
    raw = (
        '{"need_reply": true, "tts": "", '
        '"moves": [{"move": "shake_head", "ms": 1280}], "anims": []}'
    )
    parsed = parse_llm_reply(raw)
    assert parsed["json_ok"] is True
    assert parsed["reply"] == ""
    assert parsed["moves"] == [{"move": "shake_head", "ms": 1280}]


def test_memory_store_roundtrip(tmp_path, monkeypatch):
    from deskbot_server import memory_store as ms

    mem_file = tmp_path / "user_memory.json"

    def _resolve(path, device_id=None):
        return str(mem_file)

    monkeypatch.setattr(ms, "resolve_json_path", _resolve)
    monkeypatch.setattr(ms, "USER_MEMORY_FILE", str(mem_file))
    e1 = ms.add_memory("主人喜欢猫", device_id="dev1")
    assert e1["text"] == "主人喜欢猫"
    rows = ms.list_memory_for_device("dev1")
    assert len(rows) == 1
    assert ms.delete_memory(e1["id"], device_id="dev1")
    assert ms.list_memory_for_device("dev1") == []
