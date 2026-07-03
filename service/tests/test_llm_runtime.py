from __future__ import annotations

import json

import pytest


class _FakeHttpResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._body


def test_build_chat_model_keeps_openai_compatible_model_id():
    from deskbot_server.llm.runtime import build_chat_model

    assert build_chat_model("openai", "ep-202607020001") == "ep-202607020001"
    assert build_chat_model("openai", "openai/ep-202607020001") == "ep-202607020001"


def test_resolve_system_llm_config_prefers_ark_env(monkeypatch):
    from deskbot_server.llm.runtime import resolve_system_llm_config

    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.setenv("ARK_API_KEY", "ark-test-key")
    monkeypatch.setenv("ARK_BASE_URL", "https://ark.example.test/api/v3")
    monkeypatch.setattr(
        "deskbot_server.llm.runtime.load_config",
        lambda: {"llm": {"model_name": "ep-202607020001"}},
    )

    cfg = resolve_system_llm_config()

    assert cfg.api_key == "ark-test-key"
    assert cfg.api_base == "https://ark.example.test/api/v3"
    assert cfg.model == "ep-202607020001"


def test_chat_completion_stream_invokes_tts_extractor(monkeypatch):
    from deskbot_server.llm.runtime import ResolvedLlmConfig, chat_acompletion

    seen_deltas: list[str] = []

    def fake_stream(messages, cfg, *, temperature, json_mode, on_delta=None, timeout=60):
        assert json_mode is True
        chunks = ['{"tts":"', "你好", '","tools":[]}']
        for c in chunks:
            seen_deltas.append(c)
            if on_delta is not None:
                on_delta(c)
        return "".join(chunks), {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}

    monkeypatch.setattr(
        "deskbot_server.llm.runtime._request_chat_completion_stream",
        fake_stream,
    )
    cfg = ResolvedLlmConfig(
        model="qwen-flash",
        api_key="test-key",
        api_base="https://dashscope.example/v1",
        protocol="dashscope",
        source="test",
        display_name="test",
    )
    tts_seen: list[str] = []

    async def _run():
        async def on_tts(text: str) -> None:
            tts_seen.append(text)

        content, meta = await chat_acompletion(
            [{"role": "user", "content": "hi"}],
            config=cfg,
            on_tts_ready=on_tts,
        )
        return content, meta

    import asyncio

    content, meta = asyncio.run(_run())
    assert content == '{"tts":"你好","tools":[]}'
    assert tts_seen == ["你好"]
    assert meta["usage"]["total_tokens"] == 3


def test_chat_completion_posts_to_openai_compatible_endpoint(monkeypatch):
    from deskbot_server.llm.runtime import ResolvedLlmConfig, chat_completion

    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        seen["headers"] = dict(req.headers)
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeHttpResponse(
            {
                "choices": [{"message": {"content": '{"tts":"你好"}'}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
            }
        )

    monkeypatch.setattr("deskbot_server.llm.runtime.urllib.request.urlopen", fake_urlopen)
    cfg = ResolvedLlmConfig(
        model="openai/ep-202607020001",
        api_key="ark-test-key",
        api_base="https://ark.cn-beijing.volces.com/api/v3",
        protocol="openai",
        source="test",
        display_name="火山方舟",
    )

    content, meta = chat_completion(
        [{"role": "user", "content": "你好"}],
        config=cfg,
        json_mode=True,
        temperature=0.2,
    )

    assert seen["url"] == "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer ark-test-key"
    assert seen["headers"]["Content-type"] == "application/json"
    assert seen["body"] == {
        "model": "ep-202607020001",
        "messages": [{"role": "user", "content": "你好"}],
        "temperature": 0.2,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    assert content == '{"tts":"你好"}'
    assert meta["model"] == "ep-202607020001"
    assert meta["usage"] == {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8}


def test_missing_key_message_mentions_volcengine_env():
    from deskbot_server.llm.runtime import ResolvedLlmConfig, chat_completion

    cfg = ResolvedLlmConfig(
        model="ep-202607020001",
        api_key="",
        api_base="https://ark.cn-beijing.volces.com/api/v3",
        protocol="openai",
        source="test",
        display_name="火山方舟",
    )

    with pytest.raises(ValueError) as exc:
        chat_completion([{"role": "user", "content": "hi"}], config=cfg)

    assert "ARK_API_KEY" in str(exc.value)
    assert "VOLCENGINE_API_KEY" in str(exc.value)
    assert "pip install" not in str(exc.value).lower()
