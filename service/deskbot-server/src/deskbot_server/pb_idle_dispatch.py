# 调试页「自动下发 idle」：为 False 时不触发 pb_idle_silence 低头沉默舵机
_pb_idle_auto_dispatch_enabled = True


def get_pb_idle_auto_dispatch_enabled() -> bool:
    return _pb_idle_auto_dispatch_enabled


def set_pb_idle_auto_dispatch_enabled(enabled: bool) -> None:
    global _pb_idle_auto_dispatch_enabled
    _pb_idle_auto_dispatch_enabled = bool(enabled)


def pb_idle_auto_dispatch_active() -> bool:
    """实际是否触发 idle 自动下发（须自动应答与 idle 开关均开启）。"""
    from deskbot_server.auto_reply import get_asr_voice_auto_reply_enabled

    return get_pb_idle_auto_dispatch_enabled() and get_asr_voice_auto_reply_enabled()
