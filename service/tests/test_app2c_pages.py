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


PAGES = [
    "/home",
    "/voice",
    "/expr",
    "/lab",
    "/my/memories",
    "/my/reminders",
    "/my/people",
    "/my/devices",
    "/advanced",
]


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
    if payload["llm"]["system_default"]["api_key_set"]:
        assert payload["llm"]["needs_config"] is False
    else:
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
            "name": "Doubao",
            "model_name": "doubao-seed-2-1-pro-260628",
            "protocol": "ark",
            "base_url": "",
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


def test_2c_tts_config_does_not_reuse_system_ark_key(temp_db, monkeypatch):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    for name in ("DOUBAO_TTS_API_KEY", "ARK_API_KEY", "VOLCENGINE_API_KEY", "DOUBAO_API_KEY", "LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ARK_API_KEY", "ark-shared-key")
    create_user("tts-ark2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "tts-ark2c@example.com", "password": "password1234"})

    resp = client.get("/api/doubao_tts/config")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    assert payload["config"]["api_key_set"] is False


def test_2c_advanced_guides_users_to_volcengine_key_pages(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("key-guide2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "key-guide2c@example.com", "password": "password1234"})

    resp = client.get("/advanced?tab=llm")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey" in html
    assert "https://console.volcengine.com/speech/new/setting/apikeys?projectName=default" in html
    assert "复制新建的 API Key，回到这里粘贴到输入框" in html
    assert "不要把火山方舟 ARK_API_KEY 填到豆包语音 API Key" in html


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


def test_2c_lab_surfaces_device_runtime_features(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("lab2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "lab2c@example.com", "password": "password1234"})

    resp = client.get("/lab")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "设备实验台" in html
    assert "舵机控制" in html
    assert "摄像头" in html
    assert "场景编排" in html
    assert "PB 表情" in html
    assert "ASR / 流水日志" in html
    assert "/proxy/deskbot/api/servo_config" in html
    assert "/proxy/deskbot/api/device_servo" in html
    assert "/proxy/deskbot/api/device_tts" in html
    assert "/proxy/deskbot/api/device_pb_scenes" in html
    assert "/proxy/deskbot/api/device_pb_scene" in html
    assert "/proxy/deskbot/api/device_face_catalog" in html
    assert "/proxy/deskbot/api/device_face_play" in html
    assert "/proxy/deskbot/api/device_pb_anim" in html
    assert "/proxy/deskbot/api/device_pb_expr_scene" in html
    assert "/proxy/deskbot/api/scene_playbooks" in html
    assert "/proxy/deskbot/api/scene_playbook/run" in html
    assert "/proxy/deskbot/api/asr_auto_reply" in html
    assert "/proxy/deskbot/api/pb_idle_auto_dispatch" in html
    assert "/proxy/deskbot/api/camera_servo_auto_mode" in html
    assert "/proxy/deskbot/api/pipeline_recent" in html
    assert "cameraViewWsBase" in html
    assert "devicePipelineWsBase" in html


def test_2c_lab_restores_robot_motion_preview(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("lab-robot2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "lab-robot2c@example.com", "password": "password1234"})

    resp = client.get("/lab")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert '"three": "https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js"' in html
    assert "3D 形象" in html
    assert 'ref="robot3dHost"' in html
    assert "simServo" in html
    assert "robotInit3d" in html
    assert "_robotSyncServoAngles" in html
    assert "animateServoPreview" in html
    assert "drawDefaultFace" in html


def test_2c_lab_allows_browsing_before_device_selection(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("lab-browse2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "lab-browse2c@example.com", "password": "password1234"})

    resp = client.get("/lab")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "async loadAll()" in html
    assert "this.loadServoConfig({silent:true})" in html
    assert "this.loadPbScenes({silent:true})" in html
    assert "this.loadScenePlaybooks({silent:true})" in html
    assert "this.loadCameraMode({silent:true})" in html
    assert "this.loadRuntimeToggles({silent:true})" in html
    assert "async loadRuntimeToggles(options)" in html
    assert "requireDevice(options)" in html
    assert "请先选择设备" in html


def test_2c_nav_links_to_lab_page(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("lab-nav2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "lab-nav2c@example.com", "password": "password1234"})

    resp = client.get("/home")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "实验台" in html
    assert 'href="/lab"' in html


def test_2c_expr_embeds_visemesync_diy_editor_features(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("expr-diy2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "expr-diy2c@example.com", "password": "password1234"})

    resp = client.get("/expr")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "VisemeSync DIY" in html
    assert "音素表情" in html
    assert "情绪表情" in html
    assert "源码 JSON" in html
    assert "diy-canvas" in html
    assert "diyAddPrimitive" in html
    assert "diyUndo" in html
    assert "diyRedo" in html
    assert "diyAddFrame" in html
    assert "diyDuplicateFrame" in html
    assert "diyExportJson" in html
    assert "diyApplyToProfessional" in html
    assert "startDiyPointer" in html
    assert "selectedDiyPrimitive" in html
    assert "图元库" in html
    assert "颜色" in html
    assert "动画帧" in html


def test_2c_expr_layout_collapses_to_balanced_workspace():
    web_dir = Path(__file__).resolve().parents[1] / "src" / "deskbot_server" / "web"
    css = (web_dir / "static" / "theme_2c.css").read_text(encoding="utf-8")

    assert "grid-template-columns:minmax(330px,380px) minmax(0,1fr)" in css
    assert ".expr-editor{max-width:none;min-width:0;width:100%}" in css
    assert ".pro-metrics{grid-template-columns:repeat(4,minmax(0,1fr))" in css
    assert ".diy-grid{grid-template-columns:minmax(180px,220px) minmax(320px,1fr) minmax(170px,200px)" in css
    assert "@media(max-width:1280px)" in css
    assert ".exprgrid{grid-template-columns:1fr}" in css
    assert ".diy-grid{grid-template-columns:minmax(190px,240px) minmax(0,1fr)}" in css
    assert ".diy-props-panel{grid-column:1/-1}" in css
    assert "@media(max-width:900px)" in css
    assert ".app{display:block}" in css
    assert ".diy-grid{grid-template-columns:1fr}" in css


def test_2c_home_heroes_are_the_two_creative_actions_without_duplicate_camera(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("lab-home2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "lab-home2c@example.com", "password": "password1234"})

    resp = client.get("/home")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # 首页两大主动作只剩「捏表情」与「调声音」，摄像头不再作为重复入口 hero 出现
    assert "捏表情" in html
    assert "调声音" in html
    assert 'class="browse-shortcut camera-browse"' not in html
    assert "摄像头浏览" not in html
    # 摄像头仅保留在左侧 LIVE 面板里，通过「打开实验台」进入
    assert 'href="/lab?tab=camera"' in html


def test_2c_home_embeds_live_camera_view_under_stage(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("home-camera2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "home-camera2c@example.com", "password": "password1234"})

    resp = client.get("/home")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'class="stage home-console"' in html
    assert 'class="home-media"' in html
    assert 'class="media-tile"' in html
    assert "CAMERA · LIVE" in html
    assert 'class="media-stage"' in html
    assert "摄像头画面" in html
    assert "cameraViewWsBase" in html
    assert "openHomeCamera()" in html
    assert "closeHomeCamera()" in html
    assert "debug_token" in html


def test_2c_home_integrates_robot_motion_preview(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("home-robot2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "home-robot2c@example.com", "password": "password1234"})

    resp = client.get("/home")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert '"three": "https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js"' in html
    assert "window.__HOME_FACE__" in html
    assert "3D 小歪" in html
    assert 'class="home-robot-host"' in html
    assert 'ref="homeRobot3dHost"' in html
    assert "homeRobotInit3d" in html
    assert "animateHomeRobotServo" in html
    assert "playHomeRobotMotion" in html
    assert 'href="/lab?tab=servo"' in html
    # 3D 模型屏幕上的表情与「表情 · 当前」卡片同源，保持一致
    assert "updateHomeRobotFace" in html

    css = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "deskbot_server"
        / "web"
        / "static"
        / "theme_2c.css"
    ).read_text(encoding="utf-8")
    assert ".home-media" in css
    assert ".home-robot-host" in css
    assert ".home-deck-actions" in css
    assert ".media-stage" in css


def test_2c_home_is_state_aware_and_drops_duplicate_nav_panels(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("home-reminders2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "home-reminders2c@example.com", "password": "password1234"})

    resp = client.get("/home")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # 首页不再复刻侧边栏：移除快捷入口面板与数字 mini 卡
    assert 'class="gridcards"' not in html
    assert 'class="home-quick-panel"' not in html
    assert "快捷入口" not in html
    # 状态 A：未配置好时展示上手清单，引导下一步
    assert 'class="home-setup"' in html
    assert "让小歪活起来" in html
    assert "配置对话模型" in html
    assert "绑定你的小歪" in html
    # 状态 B：配置完成后展示最近动态（内容而非数字）
    assert 'class="home-recent"' in html
    assert "setupIncomplete" in html
    assert "recentMemories" in html


def test_2c_lab_accepts_initial_tab_from_query(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("lab-query2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "lab-query2c@example.com", "password": "password1234"})

    resp = client.get("/lab?tab=camera")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "initialLabTab()" in html
    assert "URLSearchParams(window.location.search)" in html
    assert "['servo','camera','scene','pb','logs']" in html


def test_2c_scene_playbook_export_plan_is_available_to_regular_user(temp_db):
    from deskbot_server.auth.device_service import bind_device
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("lab-export-admin2c@example.com", "password1234")
    user = create_user("lab-export2c@example.com", "password1234")
    bind_device(user.id, "deskbot_lab_export")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "lab-export2c@example.com", "password": "password1234"})
    client.post("/app/api/devices/select", json={"device_id": "deskbot_lab_export"})

    resp = client.post(
        "/api/scene_playbook/export_plan",
        json={
            "device_id": "deskbot_lab_export",
            "playbook": {
                "name": "demo_export",
                "title": "演示导出",
                "chunks": [{"id": "c1", "text": "你好", "servo": {"preset": "center", "ms": 500}}],
            },
        },
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    assert payload["device_id"] == "deskbot_lab_export"
    assert payload["playbook"]["name"] == "demo_export"
    assert "phases" in payload


def test_2c_voice_page_links_to_model_config_and_keeps_player(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("voice2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "voice2c@example.com", "password": "password1234"})

    resp = client.get("/voice")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "高级 · 模型配置" in html
    assert "saveTtsConfig" not in html
    assert 'ref="previewAudio"' in html
    assert 'class="preview-audio-hidden"' in html
    assert "playPreviewAudio" in html
    assert "浏览器拦截了自动播放" in html

    advanced = client.get("/advanced")
    assert advanced.status_code == 200
    advanced_html = advanced.get_data(as_text=True)
    assert "声音能力" in advanced_html
    assert "火山引擎语音技术" in advanced_html
    assert "声音高级参数" in advanced_html
    assert "saveTtsConfig" in advanced_html


def test_2c_voice_page_collapses_doubao_voice_library_by_default(temp_db):
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
    assert "已显示 [[ visibleSpeakers.length ]] / [[ filteredSpeakers.length ]] 个优选音色" in html
    assert "/api/doubao_tts/speakers?scope=consumer" in html
    assert "voiceSearch" in html
    assert "sceneOptions" in html
    assert "filteredSpeakers" in html
    assert "visibleSpeakers" in html
    assert "voiceExpanded" in html
    assert "voiceCollapsedLimit" in html
    assert "hiddenSpeakerCount" in html
    assert 'v-for="v in visibleSpeakers"' in html
    assert "展开更多音色" in html
    assert "收起音色" in html

    css = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "deskbot_server"
        / "web"
        / "static"
        / "theme_2c.css"
    ).read_text(encoding="utf-8")
    assert ".voice-expand-row" in css
    assert ".voice-expand-btn" in css


def test_2c_voice_preview_plays_inline_without_visible_audio_bar(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("voice-compact-preview2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "voice-compact-preview2c@example.com", "password": "password1234"})

    resp = client.get("/voice")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'class="voice-preview-row compact-preview"' not in html
    assert 'class="slider-card voice-volume-card"' not in html
    assert "试听音量" not in html
    assert "preview-audio-hidden" in html

    css = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "deskbot_server"
        / "web"
        / "static"
        / "theme_2c.css"
    ).read_text(encoding="utf-8")
    assert ".preview-audio-hidden" in css
    assert ".voice-preview-row.compact-preview" not in css


def test_2c_theme_uses_bold_retro_tokens():
    web_dir = Path(__file__).resolve().parents[1] / "src" / "deskbot_server" / "web"
    css = (web_dir / "static" / "theme_2c.css").read_text(encoding="utf-8")
    base = (web_dir / "templates" / "base_2c.html").read_text(encoding="utf-8")
    auth_base = (web_dir / "templates" / "auth_base.html").read_text(encoding="utf-8")

    assert "设计语言：Neo-brutalist retro console" in css
    assert "--bg:#e9e7de" in css
    assert "--panel:#fff" in css
    assert "--panel2:#f2f0e8" in css
    assert "--line:#16171b" in css
    assert "--accent:#ff6700" in css
    assert "--shadow:2px 2px 0 var(--line)" in css
    assert "background-size:32px 32px" in css
    assert ".stage .brackets span{position:absolute" in css
    assert ".face .scanline{position:absolute" in css
    assert ".stage .brackets{display:none}" not in css
    assert ".face .scanline{display:none}" not in css
    assert "final calm overrides" not in css
    assert "@media(max-width:600px)" in css
    assert "white-space:nowrap" in css
    assert ".topbar .tb-sub,.topbar .tb-clock{display:none}" in css
    assert ".heroes,.home-recent{grid-template-columns:1fr}" in css
    assert ".home-media{display:grid" in css
    assert "?v=20260708-home-media-triptych" in base
    assert "?v=20260707-modelhierarchy" in auth_base


def test_2c_voice_page_exposes_voice_clone_workflow(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("voice-clone-page2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "voice-clone-page2c@example.com", "password": "password1234"})

    resp = client.get("/voice")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "声音复刻" in html
    assert "音色名称" in html
    assert "voice_name" in html
    assert "/api/doubao_tts/voice-clone" in html
    assert "/api/doubao_tts/voice-clone/status" in html
    assert "cloneVoice" in html
    assert "checkCloneStatus" in html
    assert "applyClonedVoice" in html


def test_2c_voice_page_separates_library_and_clone_tabs_with_progress(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("voice-tabs2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "voice-tabs2c@example.com", "password": "password1234"})

    resp = client.get("/voice")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "voice-tabbar" in html
    assert "voiceTab==='library'" in html
    assert "voiceTab==='clone'" in html
    assert "直接使用" in html
    assert "clone-progress" in html
    assert "cloneProgress" in html
    assert "cloneProgressLabel" in html


def test_2c_voice_clone_upload_endpoint_uses_configured_volcengine_credentials(
    temp_db, monkeypatch
):
    from io import BytesIO

    from deskbot_server.auth.service import create_user
    from deskbot_server.tts.voice_clone import DoubaoVoiceCloneResult
    from deskbot_server.web.app import create_app

    monkeypatch.setenv("DOUBAO_TTS_APP_ID", "app-id")
    monkeypatch.setenv("DOUBAO_TTS_ACCESS_TOKEN", "access-token")
    captured = {}

    def fake_clone(
        cfg,
        *,
        audio_bytes,
        audio_format,
        language=0,
        display_name="",
        custom_speaker_id="",
        prompt_text="",
    ):
        captured["cfg"] = cfg
        captured["audio_bytes"] = audio_bytes
        captured["audio_format"] = audio_format
        captured["language"] = language
        captured["display_name"] = display_name
        captured["custom_speaker_id"] = custom_speaker_id
        captured["prompt_text"] = prompt_text
        return DoubaoVoiceCloneResult(
            speaker_id=custom_speaker_id,
            status=1,
            raw={"status": 1, "speaker_id": custom_speaker_id},
        )

    monkeypatch.setattr("deskbot_server.tts.voice_clone.clone_doubao_voice", fake_clone)
    create_user("voice-clone-api2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "voice-clone-api2c@example.com", "password": "password1234"})

    resp = client.post(
        "/api/doubao_tts/voice-clone",
        data={
            "voice_name": "小歪音色",
            "language": "0",
            "prompt_text": "你好，我是小歪。",
            "audio": (BytesIO(b"RIFF....WAVE"), "sample.wav"),
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    assert payload["speaker_id"] == "brufik_xiao_wai_yin_se"
    assert payload["status"] == 1
    assert payload["ready"] is False
    assert captured["cfg"].app_key == "app-id"
    assert captured["cfg"].access_key == "access-token"
    assert captured["cfg"].resource_id == "seed-icl-2.0"
    assert captured["audio_bytes"] == b"RIFF....WAVE"
    assert captured["audio_format"] == "wav"
    assert captured["language"] == 0
    assert captured["display_name"] == "小歪音色"
    assert captured["custom_speaker_id"] == "brufik_xiao_wai_yin_se"
    assert captured["prompt_text"] == "你好，我是小歪。"


def test_2c_voice_clone_status_endpoint_reports_ready(temp_db, monkeypatch):
    from deskbot_server.auth.service import create_user
    from deskbot_server.tts.voice_clone import DoubaoVoiceCloneResult
    from deskbot_server.web.app import create_app

    monkeypatch.setenv("DOUBAO_TTS_APP_ID", "app-id")
    monkeypatch.setenv("DOUBAO_TTS_ACCESS_TOKEN", "access-token")
    captured = {}

    def fake_status(cfg, speaker_id):
        captured["cfg"] = cfg
        captured["speaker_id"] = speaker_id
        return DoubaoVoiceCloneResult(
            speaker_id=speaker_id,
            status=4,
            raw={"status": 4, "speaker_id": speaker_id, "model_type": 5},
            model_type=5,
        )

    monkeypatch.setattr("deskbot_server.tts.voice_clone.get_doubao_voice_clone_status", fake_status)
    create_user("voice-clone-status2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "voice-clone-status2c@example.com", "password": "password1234"})

    resp = client.post("/api/doubao_tts/voice-clone/status", json={"speaker_id": "S_ready"})

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    assert payload["speaker_id"] == "S_ready"
    assert payload["ready"] is True
    assert payload["status_label"] == "可用"
    assert captured["cfg"].app_key == "app-id"
    assert captured["speaker_id"] == "S_ready"


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


def test_2c_voice_no_longer_owns_tts_config_panel(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("voice-collapse2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "voice-collapse2c@example.com", "password": "password1234"})

    resp = client.get("/voice")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert ':aria-expanded="String(configOpen)"' not in html
    assert 'v-show="configOpen"' not in html
    assert "TTS 服务配置" not in html
    assert "高级 · 模型配置" in html


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


def test_2c_expr_preview_uses_home_fallback_until_user_edits(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("expr-fallback2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "expr-fallback2c@example.com", "password": "password1234"})

    resp = client.get("/expr")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "preview.pickScene(this.scenes, this.map, 'idle')" in html
    assert "loadBrowseFallback" in html
    assert "if(!this.deviceId){ this.loadBrowseFallback(); return; }" in html
    assert "if(!this.deviceId){ this.scenes = [];" not in html
    assert "this.scenes = (r.config && r.config.length) ? r.config : [];" in html
    assert "applyPreset(name)" in html
    assert "this.editingFace = true;" in html


def test_2c_expr_allows_browsing_without_device_selection(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("expr-browse2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "expr-browse2c@example.com", "password": "password1234"})

    resp = client.get("/expr")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'data-suppress-device="1"' in html
    assert ':disabled="noDevice"' not in html
    assert "绑定设备后可配置" not in html
    assert "请先在「我的设备」选择一台设备，才能保存到设备配置。" not in html
    assert "if(!this.deviceId){ this.msg = '请先在「我的设备」选择一台设备'; return; }" not in html
    assert "if(this.deviceId) form.append('device_id', this.deviceId);" in html
    assert "faceDesignGeneratePayload(prompt)" in html
    assert "保存表情需要先选择一台设备" in html
    assert "设备预览需要先选择一台设备" in html


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
    assert "image-generation-progress" in html
    assert "imageExpressionProgress" in html
    assert "imageExpressionProgressLabel" in html
    assert "previewFrameIndex" in html
    assert "generatedPreviewSvg" in html
    assert "togglePreviewPlayback" in html
    assert "prevGeneratedFrame" in html
    assert "[[ generatedFrameLabel ]]" in html
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
    assert "还没完成 AI 能力配置" in html
    assert "需要配置大模型" not in html


def test_2c_advanced_model_config_has_clear_primary_secondary_hierarchy(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("advanced-hierarchy2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "advanced-hierarchy2c@example.com", "password": "password1234"})

    resp = client.get("/advanced?tab=llm")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "model-config-stack" in html
    assert "model-card primary-model-card" in html
    assert "model-card secondary-model-card" in html
    assert "必需配置" in html
    assert "声音能力" in html
    assert "voice-advanced-fields" in html
    assert "声音高级参数" in html

    css = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "deskbot_server"
        / "web"
        / "static"
        / "theme_2c.css"
    ).read_text(encoding="utf-8")
    assert ".primary-model-card" in css
    assert ".secondary-model-card" in css
    assert ".voice-advanced-fields" in css
    assert "details.voice-advanced-fields:not([open]) .voice-advanced-grid{display:none}" in css
    assert ".model-form-actions" in css


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
    from deskbot_server.infrastructure.tts import factory
    from deskbot_server.web.app import create_app

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


def test_face_design_generate_endpoint_allows_browsing_without_device(temp_db, monkeypatch):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    captured = {}

    def fake_completion(messages, *, device_id=None, temperature=0.7, config=None, json_mode=True):
        captured["device_id"] = device_id
        return (
            '{"name":"friendly","phonemes":[],"emotions":[{"name":"happy","title":"开心",'
            '"frames":[{"ms":300,"elements":{"mouth":[]}}]}]}',
            {"model": "openai/test", "source": "system", "display_name": "Test LLM", "usage": None},
        )

    monkeypatch.setattr("deskbot_server.llm.runtime.chat_completion", fake_completion)
    create_user("face-ai-browse2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "face-ai-browse2c@example.com", "password": "password1234"})

    resp = client.post("/api/face_design/generate", json={"prompt": "生成开心表情"})

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    assert payload["device_id"] == ""
    assert payload["design"]["emotions"][0]["name"] == "happy"
    assert captured["device_id"] is None


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
    assert "还没完成 AI 能力配置" in html
    assert "需要配置大模型" not in html
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


def test_2c_advanced_usage_has_trend_charts(temp_db):
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("usage-charts2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "usage-charts2c@example.com", "password": "password1234"})

    html = client.get("/advanced").get_data(as_text=True)
    assert "近 14 日设备用量趋势" in html
    assert "近 14 日 API Key 用量趋势" in html
    assert "<svg" in html
    assert "<polyline" in html
    assert "deviceUsageSeries" in html
    assert "keyUsageSeries" in html


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
