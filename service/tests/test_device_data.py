from __future__ import annotations


def test_ensure_device_data_initialized_copies_json(tmp_path, monkeypatch):
    from deskbot_server.utils import device_data as dd

    data_dir = tmp_path / "data"
    global_dir = data_dir / "global"
    data_dir.mkdir()
    global_dir.mkdir()
    (data_dir / "servo.json").write_text(
        '{"xMin":0,"xMax":180,"yMin":0,"yMax":180,"xReverse":0,"yReverse":0}\n', encoding="utf-8"
    )
    (data_dir / "user_memory.json").write_text('{"entries":[]}\n', encoding="utf-8")
    (global_dir / "llm_system.txt").write_text("你是测试助手\n", encoding="utf-8")

    monkeypatch.setattr(dd, "DATA_DIR", data_dir)

    assert dd.ensure_device_data_initialized("deskbot_test", "1234") is True
    dev_dir = data_dir / "deskbot_test_1234"
    assert dev_dir.is_dir()
    assert (dev_dir / "servo.json").is_file()
    assert (dev_dir / "user_memory.json").is_file()
    assert not (dev_dir / "llm_system.txt").exists()
    assert dd.ensure_device_data_initialized("deskbot_test", "1234") is False


def test_resolve_json_path_device_scoped(tmp_path, monkeypatch):
    from deskbot_server.utils import device_data as dd

    data_dir = tmp_path / "data"
    global_dir = data_dir / "global"
    data_dir.mkdir()
    global_dir.mkdir()
    monkeypatch.setattr(dd, "DATA_DIR", data_dir)

    global_path = str(data_dir / "servo.json")
    scoped = dd.resolve_json_path(global_path, "deskbot_abc", "5678")
    assert scoped == str(data_dir / "deskbot_abc_5678" / "servo.json")
    assert dd.resolve_json_path(global_path, None) == global_path

    camera_path = str(global_dir / "camera_face.json")
    assert dd.resolve_json_path(camera_path, "deskbot_abc", "5678") == camera_path


def test_load_and_save_llm_system_prompt(tmp_path, monkeypatch):
    from deskbot_server.utils import device_data as dd

    data_dir = tmp_path / "data"
    global_dir = data_dir / "global"
    data_dir.mkdir()
    global_dir.mkdir()
    (global_dir / "llm_system.txt").write_text("全局 prompt\n", encoding="utf-8")
    monkeypatch.setattr(dd, "DATA_DIR", data_dir)

    assert dd.load_llm_system_prompt() == "全局 prompt"
    dd.save_llm_system_prompt("更新后的 prompt", device_id="deskbot_x")
    assert dd.load_llm_system_prompt("deskbot_x") == "更新后的 prompt"
    assert (global_dir / "llm_system.txt").read_text(encoding="utf-8") == "更新后的 prompt\n"
    assert not (data_dir / "deskbot_x_1234" / "llm_system.txt").exists()


def test_load_llm_system_prompt_uses_global_only(tmp_path, monkeypatch):
    from deskbot_server.utils import device_data as dd

    data_dir = tmp_path / "data"
    global_dir = data_dir / "global"
    data_dir.mkdir()
    global_dir.mkdir()
    (global_dir / "llm_system.txt").write_text("全局 prompt\n", encoding="utf-8")
    monkeypatch.setattr(dd, "DATA_DIR", data_dir)

    dev_path = data_dir / "deskbot_new_1234" / "llm_system.txt"
    assert not dev_path.is_file()
    assert dd.load_llm_system_prompt("deskbot_new") == "全局 prompt"
    assert not dev_path.is_file()
