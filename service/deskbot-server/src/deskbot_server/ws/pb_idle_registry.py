"""PB 空闲计时：ws_send 与 AsrChatHub 之间的注册表，避免循环 import。"""

from __future__ import annotations

from typing import Any, Optional, Protocol

_PB_IDLE_SCHED_ATTRS = ("pb_idle_snore", "pb_idle_silence")


class _PbIdleHub(Protocol):
    pb_idle_snore: Any
    pb_idle_silence: Any

    def ws_asr_device_id(self, ws) -> Optional[str]: ...


_hub: Optional[_PbIdleHub] = None


def set_pb_idle_hub(hub: Optional[_PbIdleHub]) -> None:
    global _hub
    _hub = hub


def _notify_idle_schedulers(device_id: str) -> None:
    hub = _hub
    if hub is None or not device_id:
        return
    for attr in _PB_IDLE_SCHED_ATTRS:
        sched = getattr(hub, attr, None)
        if sched is not None:
            sched.note_activity(device_id)


def note_pb_idle_after_successful_asr_send(websocket) -> None:
    """成功下行到某条 WebSocket 后刷新各 idle 计时（仅已登记为 /asr_chat 的连接）。"""
    hub = _hub
    if hub is None:
        return
    dev = hub.ws_asr_device_id(websocket)
    if dev:
        _notify_idle_schedulers(dev)


def note_pb_idle_for_device(device_id: str) -> None:
    """按 device_id 刷新 idle 计时（设备刚接入 /asr_chat 或成功 pb 下行后调用）。"""
    hub = _hub
    if hub is not None and device_id:
        consume = getattr(hub, "consume_skip_idle_note", None)
        if callable(consume) and consume(device_id):
            return
    _notify_idle_schedulers(device_id)


def cancel_all_pb_idle_schedulers() -> None:
    """取消所有设备的 idle 自动下发计时（关闭自动应答等场景）。"""
    hub = _hub
    if hub is None:
        return
    for attr in _PB_IDLE_SCHED_ATTRS:
        sched = getattr(hub, attr, None)
        if sched is None:
            continue
        cancel_all = getattr(sched, "cancel_all", None)
        if callable(cancel_all):
            cancel_all()
