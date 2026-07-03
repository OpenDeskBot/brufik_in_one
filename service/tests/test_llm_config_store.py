"""llm_config_store 单元测试。"""
from __future__ import annotations

import json

import pytest

from deskbot_server.device_data import device_data_dir
from deskbot_server.llm_config_store import (
    LLM_MODELS_FILENAME,
    add_llm_model,
    delete_llm_model,
    get_active_llm_model,
    list_llm_models,
    set_active_llm_model,
    update_llm_model,
)


@pytest.fixture
def device_id(tmp_path, monkeypatch):
    monkeypatch.setattr("deskbot_server.device_data.DATA_DIR", tmp_path)
    monkeypatch.setattr("deskbot_server.device_data.DEVICE_DATA_ROOT", tmp_path / "device")
    return "deskbot_test001"


def test_add_list_select_delete(device_id):
    m1 = add_llm_model(
        device_id,
        name="Test Qwen",
        model_name="qwen-flash",
        protocol="openai",
        base_url="https://example.com/v1",
        api_key="sk-test1234567890",
    )
    assert m1["name"] == "Test Qwen"
    assert m1["api_key_set"] is True
    assert "sk-test" not in m1["api_key"]

    models = list_llm_models(device_id)
    assert len(models) == 1

    set_active_llm_model(device_id, m1["id"])
    active = get_active_llm_model(device_id)
    assert active is not None
    assert active.model_name == "qwen-flash"

    updated = update_llm_model(device_id, m1["id"], name="Renamed")
    assert updated is not None
    assert updated["name"] == "Renamed"

    set_active_llm_model(device_id, None)
    assert get_active_llm_model(device_id) is None

    assert delete_llm_model(device_id, m1["id"])
    assert list_llm_models(device_id) == []


def test_persisted_json(device_id):
    add_llm_model(device_id, name="A", model_name="gpt-4o", protocol="openai", api_key="key1")
    path = device_data_dir(device_id) / LLM_MODELS_FILENAME
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data["models"], list)
    assert data["models"][0]["model_name"] == "gpt-4o"
