from __future__ import annotations

import pytest


@pytest.fixture()
def memory_file(monkeypatch, tmp_path):
    path = tmp_path / "user_memory.json"
    monkeypatch.setattr(
        "deskbot_server.memory_store.resolve_json_path",
        lambda _default, device_id=None: str(
            path if not device_id else tmp_path / device_id / "user_memory.json"
        ),
    )
    return "deskbot_a"


def test_memory_crud(memory_file):
    from deskbot_server.memory_store import (
        add_memory,
        delete_memory,
        get_memory,
        list_memory_entries_for_device,
        update_memory,
    )

    e1 = add_memory("喜欢猫", device_id=memory_file)
    e2 = add_memory("住在上海", device_id=memory_file)
    assert len(list_memory_entries_for_device(memory_file)) == 2

    got = get_memory(e1["id"], device_id=memory_file)
    assert got is not None
    assert got["text"] == "喜欢猫"

    updated = update_memory(e1["id"], "喜欢狗", device_id=memory_file)
    assert updated is not None
    assert updated["text"] == "喜欢狗"

    assert delete_memory(e2["id"], device_id=memory_file)
    assert get_memory(e2["id"], device_id=memory_file) is None
    assert len(list_memory_entries_for_device(memory_file)) == 1
