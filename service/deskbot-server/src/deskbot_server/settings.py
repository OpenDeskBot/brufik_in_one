"""向后兼容：settings 辅助函数。"""

from __future__ import annotations


def _is_pb_downlink_payload(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    tp = str(payload.get("type") or "").strip()
    return tp in ("pb_start", "pb_chunk", "pb_end", "pb_single", "pb_cancel")
