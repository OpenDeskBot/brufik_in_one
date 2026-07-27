"""pytest 共享 fixture 与设备绑定辅助。"""

from __future__ import annotations

from tests.device_bind_helpers import DEFAULT_TEST_PIN, bind_device_online, mark_device_online

__all__ = ["DEFAULT_TEST_PIN", "bind_device_online", "mark_device_online"]


def pytest_configure(config):
    del config


def pytest_runtest_teardown(item, nextitem):
    del item, nextitem
    from deskbot_server.ws import device_pin as dp

    with dp._lock:
        dp._online_pins.clear()
