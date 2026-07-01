"""pb ``mic`` 字段：设备开麦/禁麦控制（无 anim/servo/audio 的空 pb_single）。"""

from __future__ import annotations

import uuid
from typing import Any, Literal, Optional

from deskbot_server.pb.shapes import PB_ACTION_DEFAULT, PB_LEVEL_TASK

PbMicMode = Literal["hold", "mute", "open"]


def parse_pb_mic(raw: Any) -> Optional[PbMicMode]:
    if raw is None or raw == "":
        return None
    s = str(raw).strip().lower()
    if s in ("hold", "mute", "open"):
        return s  # type: ignore[return-value]
    return None


def build_mic_signal_pb(
    *,
    mic: PbMicMode = "open",
    req: str | None = None,
) -> dict[str, Any]:
    """空 pb_single：仅 ``mic`` 提示，不含 anim/servo/audio。"""
    mode = parse_pb_mic(mic) or "open"
    msg: dict[str, Any] = {
        "type": "pb_single",
        "req": req or uuid.uuid4().hex[:16],
        "idx": 0,
        "chunk_ms": 1,
        "pb_ver": 2,
        "action": PB_ACTION_DEFAULT,
        "level": PB_LEVEL_TASK,
    }
    if mode != "hold":
        msg["mic"] = mode
    return msg
