"""pytest 共享 fixture 与设备绑定辅助。"""

from __future__ import annotations

from tests.device_bind_helpers import bind_device_online, mark_device_online

__all__ = ["bind_device_online", "mark_device_online"]


def pytest_configure(config):
    del config


def pytest_runtest_teardown(item, nextitem):
    del item, nextitem
    from deskbot_server.service.device_ws_service import DeviceWsService

    svc = DeviceWsService.instance()
    if svc is not None:
        svc._devices.clear()
