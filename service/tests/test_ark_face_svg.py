from __future__ import annotations

import base64
import io
import json
import re
import tempfile
from pathlib import Path

import pytest

PB_SAFE_SCENE_SHAPES = {
    "ellipse",
    "ellipse_fill",
    "circle",
    "circle_outline",
    "rect",
    "rect_outline",
    "line",
    "round_rect",
    "round_rect_outline",
}


def _all_primitives(scene):
    for frame in scene["frames"]:
        for rows in frame["elements"].values():
            for primitive in rows:
                yield primitive


def _rgb_png_bytes() -> bytes:
    from PIL import Image

    image = Image.new("RGB", (4, 2))
    image.putdata(
        [
            (255, 255, 255),
            (230, 180, 60),
            (10, 20, 30),
            (0, 0, 0),
            (120, 120, 120),
            (80, 200, 250),
            (250, 80, 80),
            (40, 40, 40),
        ]
    )
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def temp_db(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        monkeypatch.setenv("DESKBOT_DB_PATH", str(db_path))
        monkeypatch.setattr("deskbot_server.device_data.DEVICE_DATA_ROOT", Path(tmp) / "device")
        from deskbot_server.db import init_database
        from deskbot_server.db.engine import init_engine, reset_engine

        reset_engine()
        init_engine(db_path)
        init_database()
        yield db_path


def test_generate_face_svg_from_image_calls_ark_responses_and_sanitizes_svg(monkeypatch):
    from deskbot_server.ark_face_svg import generate_face_svg_from_image

    captured = {}

    def fake_transport(url, payload, api_key, timeout):
        captured["url"] = url
        captured["payload"] = payload
        captured["api_key"] = api_key
        captured["timeout"] = timeout
        return {
            "output_text": json.dumps(
                {
                    "name": "meme_smile",
                    "title": "坏笑",
                    "svg": (
                        '<svg viewBox="0 0 284 240" onclick="alert(1)">'
                        '<script>alert(1)</script><rect x="90" y="140" width="104" height="28" '
                        'rx="14" fill="#69e7ff"/></svg>'
                    ),
                    "scene": {
                        "name": "meme_smile",
                        "title": "坏笑",
                        "frames": [
                            {
                                "ms": 360,
                                "elements": {
                                    "eye_l": [{"shape": "ellipse_fill", "x": 96, "y": 92, "rw": 16, "rh": 6}],
                                    "eye_r": [{"shape": "ellipse_fill", "x": 188, "y": 92, "rw": 16, "rh": 6}],
                                    "mouth": [{"shape": "round_rect_outline", "x": 104, "y": 148, "w": 76, "h": 18, "radius": 9}],
                                    "nose": [],
                                    "extra": [],
                                },
                            }
                        ],
                    },
                },
                ensure_ascii=False,
            )
        }

    monkeypatch.setenv("ARK_API_KEY", "test-key")

    result = generate_face_svg_from_image(
        b"\x89PNG\r\n\x1a\nfake",
        "image/png",
        prompt="做成坏笑表情",
        transport=fake_transport,
    )

    assert result["model"] == "doubao-seed-2-1-pro-260628"
    assert result["scene"]["name"] == "meme_smile"
    assert result["svg"].startswith('<svg viewBox="0 0 284 240"')
    assert "<script" not in result["svg"]
    assert "onclick" not in result["svg"]
    assert captured["url"] == "https://ark.cn-beijing.volces.com/api/v3/responses"
    assert captured["api_key"] == "test-key"
    assert captured["payload"]["model"] == "doubao-seed-2-1-pro-260628"
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert captured["payload"]["max_output_tokens"] == 4096
    content = captured["payload"]["input"][0]["content"]
    assert content[0]["type"] == "input_image"
    assert content[0]["image_url"].startswith("data:image/png;base64,")
    assert content[1]["type"] == "input_text"
    assert "284x240" in content[1]["text"]
    assert "只输出 JSON" in content[1]["text"]
    assert "4 到 6 帧动画" in content[1]["text"]
    assert len(result["scene"]["frames"]) >= 4


def test_generate_face_svg_from_image_preprocesses_upload_to_black_white_face_input(monkeypatch):
    from PIL import Image

    from deskbot_server.ark_face_svg import generate_face_svg_from_image

    captured = {}

    def fake_transport(_url, payload, _api_key, _timeout):
        content = payload["input"][0]["content"]
        captured["image_url"] = content[0]["image_url"]
        captured["prompt"] = content[1]["text"]
        return {
            "output_text": json.dumps(
                {
                    "name": "soft_smile",
                    "title": "软笑",
                    "svg": '<svg viewBox="0 0 284 240"><ellipse cx="98" cy="78" rx="26" ry="22" fill="#000"/></svg>',
                    "scene": {
                        "name": "soft_smile",
                        "title": "软笑",
                        "frames": [
                            {
                                "ms": 350,
                                "elements": {
                                    "eye_l": [{"shape": "ellipse_fill", "cx": 98, "cy": 78, "rx": 26, "ry": 22}],
                                    "eye_r": [{"shape": "ellipse_fill", "cx": 186, "cy": 78, "rx": 26, "ry": 22}],
                                    "mouth": [{"shape": "line", "x1": 112, "y1": 156, "x2": 172, "y2": 156}],
                                    "nose": [],
                                    "extra": [],
                                },
                            }
                        ],
                    },
                },
                ensure_ascii=False,
            )
        }

    monkeypatch.setenv("ARK_API_KEY", "test-key")

    result = generate_face_svg_from_image(
        _rgb_png_bytes(),
        "image/png",
        prompt="保留眯笑",
        transport=fake_transport,
    )

    header, encoded = captured["image_url"].split(",", 1)
    assert header == "data:image/png;base64"
    decoded = base64.b64decode(encoded)
    assert decoded.startswith(b"\x89PNG\r\n\x1a\n")
    processed = Image.open(io.BytesIO(decoded)).convert("L")
    pixels = set(processed.getdata())
    assert pixels <= {0, 255}
    assert 0 in pixels
    assert 255 in pixels
    assert "面部表情" in captured["prompt"]
    assert "黑白" in captured["prompt"]
    assert "忽略背景、文字、水印" in captured["prompt"]
    assert result["image_preprocess"]["applied"] is True
    assert result["image_preprocess"]["mode"] == "binary_bw"


def test_generate_face_svg_from_image_coerces_flat_model_scene(monkeypatch):
    from deskbot_server.ark_face_svg import generate_face_svg_from_image

    def fake_transport(_url, _payload, _api_key, _timeout):
        return {
            "output_text": json.dumps(
                {
                    "name": "panda_shock",
                    "title": "震惊熊猫头",
                    "svg": (
                        '<svg viewBox="0 0 284 240">'
                        '<ellipse cx="90" cy="80" rx="18" ry="22" fill="#000"/>'
                        '<ellipse cx="196" cy="80" rx="18" ry="22" fill="#000"/>'
                        '<ellipse cx="142" cy="160" rx="34" ry="24" fill="#000"/>'
                        "</svg>"
                    ),
                    "scene": {
                        "name": "panda_shock",
                        "title": "震惊熊猫头",
                        "frames": [
                            {
                                "ms": 500,
                                "elements": [
                                    {"type": "ellipse_fill", "params": {"cx": 142, "cy": 120, "rx": 138, "ry": 118, "fill": "#fff"}},
                                    {"type": "ellipse_fill", "params": {"cx": 90, "cy": 80, "rx": 18, "ry": 22, "fill": "#000"}},
                                    {"type": "ellipse_fill", "params": {"cx": 196, "cy": 80, "rx": 18, "ry": 22, "fill": "#000"}},
                                    {"type": "ellipse_fill", "params": {"cx": 142, "cy": 160, "rx": 34, "ry": 24, "fill": "#000"}},
                                ],
                            }
                        ],
                    },
                },
                ensure_ascii=False,
            )
        }

    monkeypatch.setenv("ARK_API_KEY", "test-key")

    result = generate_face_svg_from_image(
        b"\x89PNG\r\n\x1a\nfake",
        "image/png",
        transport=fake_transport,
    )

    elements = result["scene"]["frames"][0]["elements"]
    assert result["scene"]["name"] == "panda_shock"
    assert elements["eye_l"][0]["x"] == 90
    assert elements["eye_r"][0]["x"] == 196
    assert elements["mouth"][0]["x"] == 142
    assert not elements["extra"]
    assert len(result["scene"]["frames"]) >= 4


def test_generate_face_svg_from_image_coerces_grouped_svg_coordinates(monkeypatch):
    from deskbot_server.ark_face_svg import generate_face_svg_from_image

    def fake_transport(_url, _payload, _api_key, _timeout):
        return {
            "output_text": json.dumps(
                {
                    "name": "panda_shock",
                    "title": "震惊熊猫头",
                    "svg": '<svg viewBox="0 0 284 240"><ellipse cx="98" cy="78" rx="26" ry="22" fill="#000"/></svg>',
                    "scene": {
                        "name": "panda_shock",
                        "title": "震惊熊猫头",
                        "frames": [
                            {
                                "ms": 500,
                                "elements": {
                                    "eye_l": [{"shape": "ellipse_fill", "cx": 98, "cy": 78, "rx": 26, "ry": 22}],
                                    "eye_r": [{"shape": "ellipse_fill", "cx": 186, "cy": 78, "rx": 26, "ry": 22}],
                                    "mouth": [{"shape": "round_rect_fill", "x": 108, "y": 122, "w": 68, "h": 72, "r": 30}],
                                    "nose": [],
                                    "extra": [],
                                },
                            }
                        ],
                    },
                },
                ensure_ascii=False,
            )
        }

    monkeypatch.setenv("ARK_API_KEY", "test-key")

    result = generate_face_svg_from_image(
        b"\x89PNG\r\n\x1a\nfake",
        "image/png",
        transport=fake_transport,
    )

    elements = result["scene"]["frames"][0]["elements"]
    assert elements["eye_l"][0] == {"shape": "ellipse_fill", "x": 98, "y": 78, "rw": 26, "rh": 22}
    assert elements["eye_r"][0] == {"shape": "ellipse_fill", "x": 186, "y": 78, "rw": 26, "rh": 22}
    assert elements["mouth"][0]["shape"] == "round_rect"
    assert elements["mouth"][0]["radius"] == 30


def test_generate_face_svg_from_image_outputs_multiframe_main_compatible_scene(monkeypatch):
    from deskbot_server.ark_face_svg import generate_face_svg_from_image
    from deskbot_server.face_expr_scenes_store import (
        design_frames_to_pb_chain,
        normalize_face_expr_scenes,
    )

    def fake_transport(_url, _payload, _api_key, _timeout):
        return {
            "output_text": json.dumps(
                {
                    "name": "panda_shock",
                    "title": "震惊熊猫头",
                    "svg": '<svg viewBox="0 0 284 240"><ellipse cx="98" cy="78" rx="26" ry="22" fill="#000"/></svg>',
                    "scene": {
                        "name": "panda_shock",
                        "title": "震惊熊猫头",
                        "frames": [
                            {
                                "ms": 500,
                                "elements": {
                                    "eye_l": [{"shape": "circle_fill", "cx": 98, "cy": 78, "r": 10}],
                                    "eye_r": [{"shape": "circle_fill", "cx": 186, "cy": 78, "r": 10}],
                                    "mouth": [{"shape": "round_rect_fill", "x": 108, "y": 122, "w": 68, "h": 72, "r": 30}],
                                    "nose": [{"shape": "circle_fill", "cx": 142, "cy": 118, "r": 5}],
                                    "extra": [],
                                },
                            }
                        ],
                    },
                },
                ensure_ascii=False,
            )
        }

    monkeypatch.setenv("ARK_API_KEY", "test-key")

    result = generate_face_svg_from_image(
        b"\x89PNG\r\n\x1a\nfake",
        "image/png",
        transport=fake_transport,
    )

    scene = result["scene"]
    normalize_face_expr_scenes([scene])
    chain = design_frames_to_pb_chain(scene["frames"], runtime_req="ark-test")
    assert chain
    assert len(scene["frames"]) >= 4
    assert len({json.dumps(frame["elements"], sort_keys=True) for frame in scene["frames"]}) > 1
    for primitive in _all_primitives(scene):
        assert primitive["shape"] in PB_SAFE_SCENE_SHAPES
        assert "cx" not in primitive
        assert "cy" not in primitive
        assert "rx" not in primitive
        assert "ry" not in primitive


def test_generate_face_svg_from_image_slugifies_invalid_model_names_for_pb_downlink(monkeypatch):
    from deskbot_server.ark_face_svg import generate_face_svg_from_image
    from deskbot_server.face_expr_scenes_store import (
        design_frames_to_pb_chain,
        normalize_face_expr_scenes,
    )

    captured = {}

    def fake_transport(_url, payload, _api_key, _timeout):
        captured["prompt"] = payload["input"][0]["content"][1]["text"]
        return {
            "output_text": json.dumps(
                {
                    "name": "软乎乎眯笑灯",
                    "title": "软乎乎眯笑灯",
                    "svg": '<svg viewBox="0 0 284 240"><ellipse cx="98" cy="78" rx="26" ry="22" fill="#000"/></svg>',
                    "scene": {
                        "name": "软乎乎-眯笑灯!",
                        "title": "软乎乎眯笑灯",
                        "frames": [
                            {
                                "ms": 350,
                                "elements": {
                                    "eye_l": [{"shape": "ellipse_fill", "cx": 98, "cy": 78, "rx": 26, "ry": 22}],
                                    "eye_r": [{"shape": "ellipse_fill", "cx": 186, "cy": 78, "rx": 26, "ry": 22}],
                                    "mouth": [{"shape": "round_rect_fill", "x": 108, "y": 148, "w": 68, "h": 10, "r": 5}],
                                    "nose": [{"shape": "circle_fill", "cx": 142, "cy": 118, "r": 5}],
                                    "extra": [],
                                },
                            }
                        ],
                    },
                },
                ensure_ascii=False,
            )
        }

    monkeypatch.setenv("ARK_API_KEY", "test-key")

    result = generate_face_svg_from_image(
        b"\x89PNG\r\n\x1a\nunique-image",
        "image/png",
        prompt="保留眯笑",
        transport=fake_transport,
    )

    scene = result["scene"]
    assert re.match(r"^image_expr_[a-f0-9]{12}$", scene["name"])
    assert result["name"] == scene["name"]
    assert scene["title"] == "软乎乎眯笑灯"
    assert "^[a-z][a-z0-9_]*$" in captured["prompt"]
    normalize_face_expr_scenes([scene])
    assert design_frames_to_pb_chain(scene["frames"], runtime_req="ark-test")
    for primitive in _all_primitives(scene):
        assert primitive["shape"] in PB_SAFE_SCENE_SHAPES
        assert "cx" not in primitive
        assert "cy" not in primitive
        assert "rx" not in primitive
        assert "ry" not in primitive


def test_face_design_generate_from_image_endpoint_requires_owned_device(temp_db, monkeypatch):
    from deskbot_server.auth.device_service import bind_device
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    def fake_generate(image_bytes, mime_type, *, prompt="", **_kwargs):
        assert image_bytes.startswith(b"\x89PNG")
        assert mime_type == "image/png"
        assert prompt == "坏笑"
        return {
            "ok": True,
            "name": "meme_smile",
            "title": "坏笑",
            "svg": '<svg viewBox="0 0 284 240"><path d="M1 1h8"/></svg>',
            "scene": {
                "name": "meme_smile",
                "title": "坏笑",
                "frames": [{"ms": 300, "elements": {"mouth": [], "eye_l": [], "eye_r": [], "nose": [], "extra": []}}],
            },
            "raw": {},
            "model": "doubao-seed-2-1-pro-260628",
            "usage": None,
        }

    monkeypatch.setattr("deskbot_server.ark_face_svg.generate_face_svg_from_image", fake_generate)
    user = create_user("face-image2c@example.com", "password1234")
    bind_device(user.id, "deskbot_image")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "face-image2c@example.com", "password": "password1234"})
    client.post("/app/api/devices/select", json={"device_id": "deskbot_image"})

    resp = client.post(
        "/api/face_design/generate-from-image",
        data={
            "device_id": "deskbot_image",
            "prompt": "坏笑",
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\nfake"), "meme.png"),
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    assert payload["scene"]["name"] == "meme_smile"
    assert payload["svg"].startswith("<svg")
    assert payload["device_id"] == "deskbot_image"


def test_face_design_generate_from_image_endpoint_allows_browsing_without_device(temp_db, monkeypatch):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    def fake_generate(image_bytes, mime_type, *, prompt="", **_kwargs):
        assert image_bytes.startswith(b"\x89PNG")
        assert mime_type == "image/png"
        assert prompt == "坏笑"
        return {
            "ok": True,
            "name": "meme_smile",
            "title": "坏笑",
            "svg": '<svg viewBox="0 0 284 240"><path d="M1 1h8"/></svg>',
            "scene": {
                "name": "meme_smile",
                "title": "坏笑",
                "frames": [{"ms": 300, "elements": {"mouth": [], "eye_l": [], "eye_r": [], "nose": [], "extra": []}}],
            },
            "raw": {},
            "model": "doubao-seed-2-1-pro-260628",
            "usage": None,
        }

    monkeypatch.setattr("deskbot_server.ark_face_svg.generate_face_svg_from_image", fake_generate)
    create_user("face-image-browse2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "face-image-browse2c@example.com", "password": "password1234"})

    resp = client.post(
        "/api/face_design/generate-from-image",
        data={
            "prompt": "坏笑",
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\nfake"), "meme.png"),
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    assert payload["device_id"] == ""
    assert payload["scene"]["name"] == "meme_smile"


def test_face_design_generate_from_image_rejects_unsupported_file(temp_db):
    from deskbot_server.auth.device_service import bind_device
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    user = create_user("face-image-bad2c@example.com", "password1234")
    bind_device(user.id, "deskbot_image_bad")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "face-image-bad2c@example.com", "password": "password1234"})
    client.post("/app/api/devices/select", json={"device_id": "deskbot_image_bad"})

    resp = client.post(
        "/api/face_design/generate-from-image",
        data={"image": (io.BytesIO(b"not an image"), "note.txt")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 400
    assert "图片" in resp.get_json()["error"]
