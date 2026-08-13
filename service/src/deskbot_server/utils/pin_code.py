"""PIN Code 校验与设备存储目录名（纯工具函数，零运行时状态）。"""

from __future__ import annotations

import re

_PIN_RE = re.compile(r"^[1-9]\d{3}$")


def normalize_pin_code(pin_code: str | None) -> str:
    return str(pin_code or "").strip()


def validate_pin_code(pin_code: str | None) -> bool:
    return bool(_PIN_RE.match(normalize_pin_code(pin_code)))


def device_storage_dirname(device_id: str, pin_code: str) -> str:
    did = str(device_id or "").strip()
    pin = normalize_pin_code(pin_code)
    if not did or not validate_pin_code(pin):
        raise ValueError("device_id and valid pin_code required")
    return f"{did}_{pin}"
