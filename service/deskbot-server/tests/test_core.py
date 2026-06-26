from __future__ import annotations

import os

from deskbot_server.core.settings import AppSettings
from deskbot_server.llm.utils import parse_llm_reply


def test_app_settings_from_config_defaults():
    cfg = {
        "server": {"port": 9000, "asr_chat_device_pb_only": True},
        "audio": {"input_codec": "opus"},
        "vad": {"mode": 3},
        "asr": {"text_filter": {"min_text_len": 2, "min_chinese_ratio": 0.0}},
        "llm": {"base_url": "https://example.com/v1", "model_name": "qwen-flash"},
        "tts": {"provider": "doubao", "sample_rate": 24000},
    }
    s = AppSettings.from_config(cfg)
    assert s.server.port == 9000
    assert s.server.asr_chat_device_pb_only is True
    assert s.audio.input_codec == "opus"
    assert s.vad.mode == 3
def test_app_settings_env_override_pb_only():
    cfg = {"server": {"asr_chat_device_pb_only": True}}
    os.environ["DESKBOT_ASR_CHAT_DEVICE_PB_ONLY"] = "0"
    try:
        s = AppSettings.from_config(cfg)
        assert s.server.asr_chat_device_pb_only is False
    finally:
        os.environ.pop("DESKBOT_ASR_CHAT_DEVICE_PB_ONLY", None)


def test_parse_llm_reply_json():
    raw = '{"need_reply": true, "tts": "你好", "moves": [], "anims": []}'
    parsed = parse_llm_reply(raw)
    assert parsed["json_ok"] is True
    assert parsed["reply"] == "你好"
    assert parsed["need_reply"] is True
    assert parsed["moves"] == []
    assert parsed["anims"] == []


def test_parse_llm_reply_plain_text_fallback():
    parsed = parse_llm_reply("纯文本回复")
    assert parsed["reply"] == "纯文本回复"
    assert parsed["json_ok"] is False


def test_asr_model_dir_ready_nested_weight(tmp_path):
    from deskbot_server.asr_model_dir import asr_model_dir_ready

    nested = tmp_path / "iic" / "SenseVoiceSmall"
    nested.mkdir(parents=True)
    assert asr_model_dir_ready(tmp_path) is False
    (nested / "model_quant.onnx").write_bytes(b"x")
    assert asr_model_dir_ready(tmp_path) is True
