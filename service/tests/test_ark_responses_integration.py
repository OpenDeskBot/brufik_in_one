"""火山方舟 ark_responses 端到端集成测试（需网络与 .env 中 ARK_API_KEY）。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest


def _ark_configured() -> bool:
    key = os.environ.get("ARK_API_KEY", "").strip()
    model = os.environ.get("ARK_MODEL", "").strip()
    return bool(key and model and not key.startswith("请替换"))


pytestmark = pytest.mark.skipif(not _ark_configured(), reason="ARK_API_KEY / ARK_MODEL 未配置")


@pytest.fixture()
def device_env(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        monkeypatch.setenv("DESKBOT_DB_PATH", str(root / "test.db"))
        monkeypatch.setattr("deskbot_server.device_data.DATA_DIR", root)
        monkeypatch.setattr("deskbot_server.device_data.DEVICE_DATA_ROOT", root / "device")
        from deskbot_server.db.engine import init_engine, reset_engine
        from deskbot_server.db import init_database

        reset_engine()
        init_engine(root / "test.db")
        init_database()
        yield "deskbot_ark_e2e"


def test_live_ark_responses_chat_completion():
    from deskbot_server.llm.runtime import ResolvedLlmConfig, chat_completion

    cfg = ResolvedLlmConfig(
        model=os.environ["ARK_MODEL"].strip(),
        api_key=os.environ["ARK_API_KEY"].strip(),
        api_base="https://ark.cn-beijing.volces.com/api/v3",
        protocol="ark_responses",
        source="test",
        display_name="DeepSeek v4 Flash",
    )
    content, meta = chat_completion(
        [{"role": "user", "content": '只输出 JSON：{"tts":"集成测试通过"}'}],
        config=cfg,
        json_mode=True,
    )
    parsed = json.loads(content)
    assert parsed["tts"] == "集成测试通过"
    assert meta["usage"]["total_tokens"] > 0


def test_live_ark_responses_stream_tts_prefetch():
    import asyncio

    from deskbot_server.llm.runtime import ResolvedLlmConfig, chat_acompletion

    cfg = ResolvedLlmConfig(
        model=os.environ["ARK_MODEL"].strip(),
        api_key=os.environ["ARK_API_KEY"].strip(),
        api_base="https://ark.cn-beijing.volces.com/api/v3",
        protocol="ark_responses",
        source="test",
        display_name="DeepSeek v4 Flash",
    )
    tts_chunks: list[str] = []

    async def on_tts(text: str) -> None:
        tts_chunks.append(text)

    async def _run():
        return await chat_acompletion(
            [{"role": "user", "content": '只输出 JSON：{"tts":"流式通过","tools":[]}'}],
            config=cfg,
            json_mode=True,
            on_tts_ready=on_tts,
        )

    content, meta = asyncio.run(_run())
    parsed = json.loads(content)
    assert parsed["tts"] == "流式通过"
    assert tts_chunks == ["流式通过"]
    assert meta["usage"]["total_tokens"] > 0


def test_api_add_select_test_ark_model(device_env, monkeypatch):
    from deskbot_server.auth.device_service import bind_device
    from deskbot_server.auth.service import create_user
    from deskbot_server.llm_config_store import get_active_llm_model
    from deskbot_server.llm.runtime import resolve_llm_config
    from deskbot_server.web.app import create_app

    device_id = device_env
    user = create_user("ark-e2e@example.com", "password1234")
    bind_device(user.id, device_id)

    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "ark-e2e@example.com", "password": "password1234"})

    create = client.post(
        f"/app/api/llm-models?device_id={device_id}",
        json={
            "name": "DeepSeek v4 Flash",
            "model_name": os.environ["ARK_MODEL"].strip(),
            "protocol": "ark_responses",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "api_key": os.environ["ARK_API_KEY"].strip(),
        },
    )
    assert create.status_code == 200, create.get_data(as_text=True)
    model_id = create.get_json()["model"]["id"]

    listed = client.get(f"/app/api/llm-models?device_id={device_id}")
    payload = listed.get_json()
    assert payload["ok"] is True
    assert "ark_responses" in payload["supported_protocols"]

    tested = client.post(
        f"/app/api/llm-models/test?device_id={device_id}",
        json={
            "model_id": model_id,
            "prompt": "你好，请用一句话介绍你自己。",
        },
    )
    assert tested.status_code == 200, tested.get_data(as_text=True)
    test_payload = tested.get_json()
    assert test_payload["ok"] is True
    assert len(test_payload["reply"]) > 0

    selected = client.post(
        f"/app/api/llm-models/select?device_id={device_id}",
        json={"model_id": model_id},
    )
    assert selected.status_code == 200

    active = get_active_llm_model(device_id)
    assert active is not None
    assert active.protocol == "ark_responses"

    resolved = resolve_llm_config(device_id)
    assert resolved.protocol == "ark_responses"
    assert resolved.model == os.environ["ARK_MODEL"].strip()


def test_openai_adapter_with_ark_device(device_env, monkeypatch):
    import asyncio

    from deskbot_server.auth.device_service import bind_device
    from deskbot_server.auth.service import create_user
    from deskbot_server.config import load_config
    from deskbot_server.core.settings import AppSettings
    from deskbot_server.infrastructure.llm.openai_compat import OpenAiLlmAdapter
    from deskbot_server.llm_config_store import add_llm_model, set_active_llm_model

    device_id = device_env
    create_user("ark-adapter@example.com", "password1234")
    bind_device(create_user("ark-adapter2@example.com", "password1234").id, device_id)

    model = add_llm_model(
        device_id,
        name="DeepSeek v4 Flash",
        model_name=os.environ["ARK_MODEL"].strip(),
        protocol="ark_responses",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key=os.environ["ARK_API_KEY"].strip(),
    )
    set_active_llm_model(device_id, model["id"])

    settings = AppSettings.from_config(load_config())
    adapter = OpenAiLlmAdapter(settings)

    async def _run():
        return await adapter.complete("说三个字：测试通过", device_id=device_id)

    answer = asyncio.run(_run())
    parsed = json.loads(answer)
    assert isinstance(parsed.get("tts"), str)
    assert len(parsed["tts"]) > 0


def test_debug_llm_chat_with_ark_device(device_env):
    from deskbot_server.auth.device_service import bind_device
    from deskbot_server.auth.service import create_user
    from deskbot_server.llm_config_store import add_llm_model, set_active_llm_model
    from deskbot_server.web.app import create_app

    device_id = device_env
    user = create_user("ark-debug@example.com", "password1234")
    bind_device(user.id, device_id)

    model = add_llm_model(
        device_id,
        name="DeepSeek v4 Flash",
        model_name=os.environ["ARK_MODEL"].strip(),
        protocol="ark_responses",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key=os.environ["ARK_API_KEY"].strip(),
    )
    set_active_llm_model(device_id, model["id"])

    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "ark-debug@example.com", "password": "password1234"})

    resp = client.post(
        "/api/llm/chat",
        json={"text": "你好", "device_id": device_id},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    payload = resp.get_json()
    assert payload["ok"] is True
    assert len(payload.get("reply") or payload.get("raw") or "") > 0
