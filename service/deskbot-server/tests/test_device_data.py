from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_ensure_device_data_initialized_copies_json(tmp_path, monkeypatch):
    from deskbot_server import device_data as dd

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "servo.json").write_text('{"xMin":0,"xMax":180,"yMin":0,"yMax":180,"xReverse":0,"yReverse":0}\n', encoding="utf-8")
    (data_dir / "user_memory.json").write_text('{"entries":[]}\n', encoding="utf-8")
    (data_dir / "llm_system.txt").write_text("你是测试助手\n", encoding="utf-8")

    monkeypatch.setattr(dd, "DATA_DIR", data_dir)
    monkeypatch.setattr(dd, "DEVICE_DATA_ROOT", data_dir / "device")

    assert dd.ensure_device_data_initialized("deskbot_test") is True
    dev_dir = data_dir / "device" / "deskbot_test"
    assert dev_dir.is_dir()
    assert (dev_dir / "servo.json").is_file()
    assert (dev_dir / "user_memory.json").is_file()
    assert (dev_dir / "llm_system.txt").read_text(encoding="utf-8") == "你是测试助手\n"
    assert dd.ensure_device_data_initialized("deskbot_test") is False


def test_resolve_json_path_device_scoped(tmp_path, monkeypatch):
    from deskbot_server import device_data as dd

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(dd, "DATA_DIR", data_dir)
    monkeypatch.setattr(dd, "DEVICE_DATA_ROOT", data_dir / "device")

    global_path = str(data_dir / "servo.json")
    scoped = dd.resolve_json_path(global_path, "deskbot_abc")
    assert scoped == str(data_dir / "device" / "deskbot_abc" / "servo.json")
    assert dd.resolve_json_path(global_path, None) == global_path


def test_load_and_save_llm_system_prompt(tmp_path, monkeypatch):
    from deskbot_server import device_data as dd

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "llm_system.txt").write_text("全局 prompt\n", encoding="utf-8")
    monkeypatch.setattr(dd, "DATA_DIR", data_dir)
    monkeypatch.setattr(dd, "DEVICE_DATA_ROOT", data_dir / "device")

    assert dd.load_llm_system_prompt() == "全局 prompt"
    dd.save_llm_system_prompt("设备 prompt", device_id="deskbot_x")
    assert dd.load_llm_system_prompt("deskbot_x") == "设备 prompt"
    assert (data_dir / "device" / "deskbot_x" / "llm_system.txt").is_file()


def test_load_llm_system_prompt_copies_missing_device_file(tmp_path, monkeypatch):
    from deskbot_server import device_data as dd

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "llm_system.txt").write_text("全局 prompt\n", encoding="utf-8")
    monkeypatch.setattr(dd, "DATA_DIR", data_dir)
    monkeypatch.setattr(dd, "DEVICE_DATA_ROOT", data_dir / "device")

    dev_path = data_dir / "device" / "deskbot_new" / "llm_system.txt"
    assert not dev_path.is_file()
    assert dd.load_llm_system_prompt("deskbot_new") == "全局 prompt"
    assert dev_path.is_file()
    assert dev_path.read_text(encoding="utf-8") == "全局 prompt\n"
