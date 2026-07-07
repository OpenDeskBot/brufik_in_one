from __future__ import annotations

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


PAGES = ["/home", "/voice", "/expr", "/my/memories", "/my/reminders", "/my/people", "/my/devices", "/advanced"]


@pytest.mark.parametrize("path", PAGES)
def test_2c_pages_redirect_when_anonymous(temp_db, path):
    from deskbot_server.web.app import create_app

    client = create_app().test_client()
    assert client.get(path).status_code == 302


@pytest.mark.parametrize("path", PAGES)
def test_2c_pages_render_when_logged_in(temp_db, path):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("u2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "u2c@example.com", "password": "password1234"})
    resp = client.get(path)
    assert resp.status_code == 200


def test_2c_advanced_json_apis(temp_db):
    from deskbot_server.auth.device_service import bind_device
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    user = create_user("advanced2c@example.com", "password1234")
    bind_device(user.id, "deskbot_adv")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "advanced2c@example.com", "password": "password1234"})
    client.post("/app/api/devices/select", json={"device_id": "deskbot_adv"})

    summary = client.get("/api/advanced")
    assert summary.status_code == 200
    payload = summary.get_json()
    assert payload["ok"] is True
    assert payload["current_device_id"] == "deskbot_adv"
    assert payload["llm"]["needs_config"] is True
    assert "大模型配置" in payload["llm"]["config_message"]

    profile = client.patch("/api/advanced/profile", json={"display_name": "新名字"})
    assert profile.status_code == 200
    assert profile.get_json()["user"]["display_name"] == "新名字"

    key_resp = client.post("/api/advanced/api-keys", json={"name": "front"})
    assert key_resp.status_code == 200
    key_payload = key_resp.get_json()
    assert key_payload["raw_key"].startswith("odk_")
    key_id = key_payload["api_key"]["id"]
    assert client.delete(f"/api/advanced/api-keys/{key_id}").status_code == 200

    model = client.post(
        "/app/api/llm-models?device_id=deskbot_adv",
        json={
            "name": "Qwen",
            "model_name": "qwen-flash",
            "protocol": "openai",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "sk-test",
        },
    )
    assert model.status_code == 200
    model_id = model.get_json()["model"]["id"]
    assert client.post(
        "/app/api/llm-models/select?device_id=deskbot_adv",
        json={"model_id": model_id},
    ).status_code == 200
    configured = client.get("/api/advanced").get_json()["llm"]
    assert configured["needs_config"] is False
    assert configured["active"]["api_key_set"] is True
    assert client.delete(f"/app/api/llm-models/{model_id}?device_id=deskbot_adv").status_code == 200


def test_2c_advanced_debug_is_inline_not_old_debug_links(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("debug2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "debug2c@example.com", "password": "password1234"})

    resp = client.get("/advanced")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "/debug/devices" not in html
    assert "/debug/llm" not in html
    assert "/debug/tts" not in html
    assert "/debug/simulation" not in html
    assert "runDebugHealth" in html
    assert "runDebugLlm" in html
    assert "runDebugTts" in html
    assert "runDebugSimulation" in html


def test_2c_voice_page_exposes_tts_config_and_player(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("voice2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "voice2c@example.com", "password": "password1234"})

    resp = client.get("/voice")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "TTS 服务配置" in html
    assert 'ref="previewAudio"' in html
    assert "saveTtsConfig" in html
    assert "playPreviewAudio" in html


def test_2c_voice_page_exposes_full_doubao_voice_library_controls(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("voice-library2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "voice-library2c@example.com", "password": "password1234"})

    resp = client.get("/voice")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "优选音色" in html
    assert "只展示 2.0 可用音色，已过滤旧版、测试和不稳定音色" in html
    assert "已显示 [[ filteredSpeakers.length ]] / [[ speakers.length ]] 个优选音色" in html
    assert "/api/doubao_tts/speakers?scope=consumer" in html
    assert "voiceSearch" in html
    assert "sceneOptions" in html
    assert "filteredSpeakers" in html


def test_doubao_tts_speakers_api_can_return_consumer_ready_presets(temp_db):
    import json

    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    speakers_path = Path(__file__).resolve().parents[1] / "data" / "doubao_tts_speakers.json"
    rows = json.loads(speakers_path.read_text(encoding="utf-8"))
    expected = [
        row
        for row in rows
        if row.get("resource_id") == "seed-tts-2.0"
        and (row.get("scene") or "").strip()
        and ("_uranus_" in row.get("id", "") or row.get("id", "").startswith("saturn_"))
    ]

    create_user("voice-consumer-api2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "voice-consumer-api2c@example.com", "password": "password1234"})

    resp = client.get("/api/doubao_tts/speakers?scope=consumer")

    assert resp.status_code == 200
    payload = resp.get_json()
    ids = {item["id"] for item in payload["speakers"]}
    assert payload["ok"] is True
    assert len(payload["speakers"]) == len(expected)
    assert "zh_female_vv_uranus_bigtts" in ids
    assert "zh_female_vv_mars_bigtts" not in ids
    assert "ICL_zh_male_bujiqingnian_tob" not in ids
    assert all(item["resource_id"] == "seed-tts-2.0" for item in payload["speakers"])
    assert all((item["scene"] or "").strip() for item in payload["speakers"])


def test_doubao_tts_speakers_api_returns_full_local_preset_file(temp_db):
    import json

    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    speakers_path = Path(__file__).resolve().parents[1] / "data" / "doubao_tts_speakers.json"
    expected_count = len(json.loads(speakers_path.read_text(encoding="utf-8")))

    create_user("voice-all-api2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "voice-all-api2c@example.com", "password": "password1234"})

    resp = client.get("/api/doubao_tts/speakers")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    assert len(payload["speakers"]) == expected_count
    assert expected_count >= 300


def test_2c_voice_tts_config_panel_is_collapsible(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("voice-collapse2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "voice-collapse2c@example.com", "password": "password1234"})

    resp = client.get("/voice")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert ':aria-expanded="String(configOpen)"' in html
    assert 'v-show="configOpen"' in html
    assert "展开配置" in html
    assert "收起配置" in html


def test_2c_voice_tts_synthesize_endpoint_returns_wav(temp_db, monkeypatch):
    from deskbot_server.auth.service import create_user
    from deskbot_server.tts.doubao import DoubaoTtsResult
    from deskbot_server.web.app import create_app

    async def fake_synthesize(text, cfg):
        assert text == "试听"
        assert cfg.api_key == "tts-key"
        assert cfg.speaker == "voice-id"
        assert cfg.resource_id == "seed-tts-2.0"
        return DoubaoTtsResult(pcm=b"\x00\x00" * 120, sample_rate=24000, elapsed_ms=7)

    monkeypatch.setattr("deskbot_server.tts.doubao.synthesize_doubao_tts", fake_synthesize)
    create_user("voice-api2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "voice-api2c@example.com", "password": "password1234"})

    resp = client.post(
        "/api/doubao_tts/synthesize",
        json={
            "text": "试听",
            "api_key": "tts-key",
            "speaker": "voice-id",
            "resource_id": "seed-tts-2.0",
        },
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    assert payload["wav_base64"]
    assert payload["sample_rate"] == 24000


def test_2c_expr_page_exposes_real_face_editor_controls(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("expr-editor2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "expr-editor2c@example.com", "password": "password1234"})

    resp = client.get("/expr")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "捏脸参数" in html
    assert 'v-model.number="customFace.eyeGap"' in html
    assert 'v-model.number="customFace.mouthCurve"' in html
    assert "customPreviewSvg" in html
    assert "buildCustomScene" in html
    assert "faceFromScene" in html
    assert "sendPreviewToDevice" in html
    assert "/api/device_pb_expr_scene" in html


def test_2c_expr_page_exposes_professional_design_tab(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("expr-pro-design2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "expr-pro-design2c@example.com", "password": "password1234"})

    resp = client.get("/expr")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "专业设计" in html
    assert "VisemeSync JSON" in html
    assert "exprTab" in html
    assert "importProfessionalFile" in html
    assert "saveProfessionalDesign" in html
    assert "exportProfessionalDesign" in html
    assert "AI 辅助生成" in html
    assert "generateProfessionalDesign" in html
    assert "/api/face_design/generate" in html
    assert "exprTab==='image'" in html
    assert "图片生成 / ARK SEED" in html
    assert "图片表情包生成" in html
    assert "generateImageExpression" in html
    assert "/api/face_design/generate-from-image" in html
    assert html.index("图片生成 / ARK SEED") < html.index("专业设计 / VISEMESYNC")
    assert "preserveMap:true" in html
    assert "/api/face_mouth_by_phoneme" in html


def test_2c_face_config_apis_are_available_to_regular_user(
    temp_db, tmp_path, monkeypatch
):
    import json

    from deskbot_server.auth.device_service import bind_device
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    monkeypatch.setattr("deskbot_server.device_data.DATA_DIR", tmp_path)
    monkeypatch.setattr("deskbot_server.device_data.DEVICE_DATA_ROOT", tmp_path / "device")
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    (global_dir / "deskbot-face.json").write_text(
        json.dumps({"name": "qa", "phonemes": [], "emotions": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    from deskbot_server.face_design_store import clear_face_design_cache

    clear_face_design_cache()
    create_user("face-admin2c@example.com", "password1234")
    user = create_user("face-member2c@example.com", "password1234")
    bind_device(user.id, "deskbot_face_api")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "face-member2c@example.com", "password": "password1234"})
    client.post("/app/api/devices/select", json={"device_id": "deskbot_face_api"})

    get_scenes = client.get("/api/face_expr_scenes")
    assert get_scenes.status_code == 200
    assert get_scenes.get_json()["ok"] is True

    scene = {
        "name": "happy",
        "title": "开心",
        "frames": [
            {
                "ms": 300,
                "elements": {"mouth": [], "nose": [], "eye_l": [], "eye_r": [], "extra": []},
            }
        ],
    }
    save_scenes = client.post(
        "/api/face_expr_scenes",
        json={"device_id": "deskbot_face_api", "scenes": [scene]},
    )
    assert save_scenes.status_code == 200
    assert save_scenes.get_json()["config"][0]["name"] == "happy"

    save_mouth = client.post(
        "/api/face_mouth_by_phoneme",
        json={
            "device_id": "deskbot_face_api",
            "mouth_by_phoneme_groups": [
                {
                    "states": ["a"],
                    "elements": [
                        {"shape": "round_rect_outline", "x": 112, "y": 148, "w": 60, "h": 28}
                    ],
                    "offset": {"x": 0, "y": 0},
                }
            ],
        },
    )
    assert save_mouth.status_code == 200
    assert save_mouth.get_json()["mouth_by_phoneme_groups"][0]["states"] == ["a"]


def test_2c_advanced_keeps_heavy_features_collapsed(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("advanced-collapse2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "advanced-collapse2c@example.com", "password": "password1234"})

    resp = client.get("/advanced")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "advancedOpen" in html
    assert "toggleAdvanced" in html
    assert "v-show=\"advancedOpen.keys\"" in html
    assert "v-show=\"advancedOpen.llm\"" in html
    assert "v-show=\"advancedOpen.account\"" in html
    assert "v-show=\"advancedOpen.debug\"" in html
    assert "展开配置" in html
    assert "收起配置" in html
    assert "/api/tts/phoneme_tts" in html
    assert "/api/paddlespeech/phoneme_tts" not in html
    assert "需要配置大模型" in html
    assert "llm.needs_config" in html
    assert "advancedOpen.llm=true" in html


def test_2c_consumer_apis_are_not_developer_locked(temp_db, monkeypatch):
    from deskbot_server.auth.device_service import bind_device
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    def fake_completion(messages, *, device_id=None, temperature=0.7, config=None, json_mode=True):
        return (
            '{"name":"friendly","phonemes":[],"emotions":[{"name":"happy","title":"开心",'
            '"frames":[{"ms":300,"elements":{"mouth":[]}}]}]}',
            {"model": "openai/test", "source": "device", "display_name": "Test LLM"},
        )

    monkeypatch.setattr("deskbot_server.llm.runtime.chat_completion", fake_completion)
    create_user("consumer-admin2c@example.com", "password1234")
    user = create_user("consumer-member2c@example.com", "password1234")
    bind_device(user.id, "deskbot_consumer_api")
    app = create_app()
    client = app.test_client()
    client.post(
        "/login",
        data={"email": "consumer-member2c@example.com", "password": "password1234"},
    )
    client.post("/app/api/devices/select", json={"device_id": "deskbot_consumer_api"})

    assert client.get("/api/health").status_code == 200
    assert client.get("/api/debug/ws_token").status_code == 200
    assert client.get("/api/doubao_tts/speakers?scope=consumer").status_code == 200

    ai = client.post(
        "/api/face_design/generate",
        json={"device_id": "deskbot_consumer_api", "prompt": "生成开心表情"},
    )
    assert ai.status_code == 200
    assert ai.get_json()["ok"] is True


def test_2c_debug_phoneme_endpoint_returns_json_when_tts_adapter_fails(
    temp_db, monkeypatch
):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    from deskbot_server.infrastructure.tts import factory

    def fail_adapter(_settings):
        raise RuntimeError("no tts adapter")

    monkeypatch.setattr(factory, "build_tts_adapter", fail_adapter)
    create_user("phoneme-debug2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "phoneme-debug2c@example.com", "password": "password1234"})

    resp = client.post("/api/tts/phoneme_tts", json={"text": "你好"})

    assert resp.status_code == 502
    assert resp.is_json
    payload = resp.get_json()
    assert payload["ok"] is False
    assert "no tts adapter" in payload["error"]


def test_face_design_generate_endpoint_uses_llm_and_returns_design(temp_db, monkeypatch):
    from deskbot_server.auth.device_service import bind_device
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    captured = {}

    def fake_completion(messages, *, device_id=None, temperature=0.7, config=None, json_mode=True):
        captured["messages"] = messages
        captured["device_id"] = device_id
        captured["temperature"] = temperature
        captured["json_mode"] = json_mode
        return (
            '{"name":"friendly","phonemes":[{"name":"a","alias":["a1"],"title":"a",'
            '"frames":[{"ms":120,"elements":{"mouth":[{"shape":"ellipse_fill","x":142,"y":160,"rw":18,"rh":9}]}}]}],'
            '"emotions":[{"name":"happy","title":"开心","frames":[{"ms":300,"elements":{"mouth":[{"shape":"line","x1":110,"y1":160,"x2":174,"y2":160}]}}]}]}',
            {"model": "openai/test", "source": "device", "display_name": "Test LLM", "usage": {"total_tokens": 12}},
        )

    monkeypatch.setattr("deskbot_server.llm.runtime.chat_completion", fake_completion)
    user = create_user("face-ai2c@example.com", "password1234")
    bind_device(user.id, "deskbot_ai")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "face-ai2c@example.com", "password": "password1234"})

    resp = client.post(
        "/api/face_design/generate",
        json={"device_id": "deskbot_ai", "prompt": "做一个开心、圆润、适合儿童的表情包"},
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    assert payload["design"]["name"] == "friendly"
    assert payload["design"]["phonemes"][0]["name"] == "a"
    assert payload["design"]["emotions"][0]["name"] == "happy"
    assert payload["model"] == "openai/test"
    assert captured["device_id"] == "deskbot_ai"
    assert captured["json_mode"] is True
    joined = "\n".join(m["content"] for m in captured["messages"])
    assert "VisemeSync JSON" in joined
    assert "phonemes" in joined
    assert "emotions" in joined


def test_2c_face_preview_helper_exposes_frame_reader():
    helper = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "deskbot_server"
        / "web"
        / "static"
        / "face_preview_2c.js"
    ).read_text(encoding="utf-8")

    assert "frameElements," in helper


def test_2c_expr_ai_generation_reminds_llm_config_required(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("expr-ai-reminder2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "expr-ai-reminder2c@example.com", "password": "password1234"})

    resp = client.get("/expr")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "需要配置大模型" in html
    assert "loadLlmConfigStatus" in html
    assert "llmNeedsConfig" in html
    assert "/advanced" in html


def test_2c_advanced_llm_form_has_test_connection(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("llm-test2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "llm-test2c@example.com", "password": "password1234"})

    resp = client.get("/advanced")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "测试连接" in html
    assert "testLlmModel" in html
    assert "/app/api/llm-models/test" in html
    assert 'v-model="llmForm.test_prompt"' in html


def test_2c_advanced_usage_includes_daily_breakdown(temp_db):
    from deskbot_server.auth.device_service import bind_device
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    user = create_user("usage-daily2c@example.com", "password1234")
    bind_device(user.id, "deskbot_usage")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "usage-daily2c@example.com", "password": "password1234"})
    client.post("/app/api/devices/select", json={"device_id": "deskbot_usage"})

    payload = client.get("/api/advanced").get_json()
    assert "device_daily_rows" in payload["usage"]
    assert "key_daily_rows" in payload["usage"]
    assert isinstance(payload["usage"]["device_daily_rows"], list)

    html = client.get("/advanced").get_data(as_text=True)
    assert "近 14 日设备明细" in html
    assert "近 14 日 API Key 明细" in html


def test_old_app_pages_removed_but_apis_kept(temp_db):
    from deskbot_server.auth.device_service import bind_device
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    user = create_user("retire-app@example.com", "password1234")
    bind_device(user.id, "deskbot_retire")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "retire-app@example.com", "password": "password1234"})
    client.post("/app/api/devices/select", json={"device_id": "deskbot_retire"})

    for page in ["/app/", "/app/usage", "/app/settings", "/app/llm-models", "/app/scheduled-tasks", "/app/face-profiles", "/app/configure", "/app/memories", "/app/devices"]:
        assert client.get(page).status_code == 404, page

    assert client.get("/app/api/scheduled-tasks").status_code == 200
    assert client.get("/app/api/llm-models?device_id=deskbot_retire").status_code == 200
    assert client.get("/app/api/tts/speakers").status_code == 200
