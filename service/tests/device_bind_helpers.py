"""测试辅助：设备在线与绑定。"""

from __future__ import annotations


def mark_device_online(device_id: str) -> None:
    from deskbot_server.service.device_ws_service import DeviceWsService

    svc = DeviceWsService.instance()
    if svc is None:
        svc = DeviceWsService()
    svc._mark_device_online(device_id)


def bind_device_online(user_id: str, device_id: str, **kwargs):
    from deskbot_server.service.user_service import UserService

    mark_device_online(device_id)
    return UserService().bind_device(user_id, device_id, **kwargs)
