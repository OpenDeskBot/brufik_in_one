from __future__ import annotations


def test_memory_crud():
    """device_memory_mapper 高层 API CRUD 测试（需要 DB）。"""
    from deskbot_server.dao.device_memory_mapper import (
        add_memory,
        delete_by_device,
        delete_memory,
        get_memory,
        list_memory_for_device,
        update_memory,
    )

    delete_by_device("test_dev_mem")

    e1 = add_memory("喜欢猫", device_id="test_dev_mem")
    e2 = add_memory("住在上海", device_id="test_dev_mem")
    assert len(list_memory_for_device("test_dev_mem")) == 2

    got = get_memory(e1["id"], device_id="test_dev_mem")
    assert got is not None
    assert got["text"] == "喜欢猫"

    updated = update_memory(e1["id"], "喜欢狗", device_id="test_dev_mem")
    assert updated is not None
    assert updated["text"] == "喜欢狗"

    assert delete_memory(e2["id"], device_id="test_dev_mem")
    assert get_memory(e2["id"], device_id="test_dev_mem") is None
    assert len(list_memory_for_device("test_dev_mem")) == 1

    # cleanup
    delete_by_device("test_dev_mem")
