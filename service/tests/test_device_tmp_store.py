from __future__ import annotations

import pytest


@pytest.fixture()
def tmp_device(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "deskbot_server.device_tmp_store.device_data_dir",
        lambda device_id: tmp_path / device_id,
    )
    return "deskbot_test"


def test_write_read_tmp_file(tmp_device):
    from deskbot_server.device_tmp_store import read_device_tmp_file, write_device_tmp_file

    write_device_tmp_file(tmp_device, "notes/hello.txt", "你好")
    out = read_device_tmp_file(tmp_device, "notes/hello.txt")
    assert out["content"] == "你好"


def test_tmp_path_traversal_blocked(tmp_device):
    from deskbot_server.device_tmp_store import resolve_device_tmp_path

    with pytest.raises(ValueError):
        resolve_device_tmp_path(tmp_device, "../secret.txt")
