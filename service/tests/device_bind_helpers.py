"""测试辅助：设备在线 PIN 与绑定。"""

from __future__ import annotations

DEFAULT_TEST_PIN = "1234"


def mark_device_online(device_id: str, pin_code: str = DEFAULT_TEST_PIN) -> None:
    from deskbot_server.ws.device_pin import set_online_pin

    set_online_pin(device_id, pin_code)


def bind_device_online(user_id: str, device_id: str, pin_code: str = DEFAULT_TEST_PIN, **kwargs):
    from deskbot_server.auth.device_service import bind_device

    mark_device_online(device_id, pin_code)
    return bind_device(user_id, device_id, pin_code, **kwargs)
