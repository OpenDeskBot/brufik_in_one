from __future__ import annotations

import base64
import json


def test_clone_doubao_voice_posts_v3_payload(monkeypatch):
    from deskbot_server.tts.voice_clone import (
        DoubaoVoiceCloneConfig,
        clone_doubao_voice,
        custom_speaker_id_from_name,
    )

    captured = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"status_code":0,"speaker_id":"brufik_wo_de_sheng_yin","status":1}'

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.headers)
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("deskbot_server.tts.voice_clone.urlopen", fake_urlopen)
    cfg = DoubaoVoiceCloneConfig(app_key="app-id", access_key="access-token")

    result = clone_doubao_voice(
        cfg,
        audio_bytes=b"RIFF....WAVE",
        audio_format="wav",
        language=0,
        display_name="我的声音",
        custom_speaker_id=custom_speaker_id_from_name("我的声音"),
        prompt_text="这是一段训练文本",
    )

    assert captured["url"] == "https://openspeech.bytedance.com/api/v3/tts/voice_clone"
    assert captured["headers"]["X-api-app-key"] == "app-id"
    assert captured["headers"]["X-api-access-key"] == "access-token"
    assert captured["headers"]["X-api-resource-id"] == "seed-icl-2.0"
    assert captured["payload"]["speaker_id"] == "custom_speaker_id"
    assert captured["payload"]["custom_speaker_id"] == "brufik_wo_de_sheng_yin"
    assert captured["payload"]["language"] == 0
    assert captured["payload"]["display_name"] == "我的声音"
    assert captured["payload"]["audio"]["format"] == "wav"
    assert captured["payload"]["audio"]["text"] == "这是一段训练文本"
    assert captured["payload"]["audio"]["data"] == base64.b64encode(b"RIFF....WAVE").decode("ascii")
    assert result.speaker_id == "brufik_wo_de_sheng_yin"
    assert result.ready is False
    assert result.status == 1
    assert result.status_label == "训练中"


def test_get_doubao_voice_clone_status_normalizes_speaker_status(monkeypatch):
    from deskbot_server.tts.voice_clone import DoubaoVoiceCloneConfig, get_doubao_voice_clone_status

    captured = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return (
                b'{"speaker_status":[{"speaker_id":"S_ready","status":4,'
                b'"model_type":5,"available_training_times":8}]}'
            )

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.headers)
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("deskbot_server.tts.voice_clone.urlopen", fake_urlopen)
    cfg = DoubaoVoiceCloneConfig(app_key="app-id", access_key="access-token")

    result = get_doubao_voice_clone_status(cfg, "S_ready")

    assert captured["url"] == "https://openspeech.bytedance.com/api/v3/tts/get_voice"
    assert captured["headers"]["X-api-resource-id"] == "seed-icl-2.0"
    assert captured["payload"] == {"speaker_id": "S_ready"}
    assert result.speaker_id == "S_ready"
    assert result.status == 4
    assert result.status_label == "可用"
    assert result.ready is True
    assert result.model_type == 5
