from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

import pytest


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
    content = captured["payload"]["input"][0]["content"]
    assert content[0]["type"] == "input_image"
    assert content[0]["image_url"].startswith("data:image/png;base64,")
    assert content[1]["type"] == "input_text"
    assert "284x240" in content[1]["text"]
    assert "只输出 JSON" in content[1]["text"]


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
