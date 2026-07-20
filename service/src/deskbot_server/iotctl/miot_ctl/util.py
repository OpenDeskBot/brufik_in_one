"""配置、值解析、spec 查找等工具函数。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from miot.spec import MIoTSpecDeviceLite

MIOT_SPEC_CODES = {
    -704042011: "设备离线",
    -704042001: "未找到设备",
    -704090001: "未找到设备",
    -704040003: "属性不存在",
    -704040005: "方法不存在",
    -704030013: "属性不可读",
    -704030023: "属性不可写",
    -704220043: "属性值不正确",
    -704053100: "无法执行此操作",
    -704083036: "操作超时",
    -704012906: "认证失败",
    -705201023: "写属性失败",
    -706012023: "写属性失败",
    -705201015: "方法执行失败",
}

MIOT_OK_CODES = frozenset({0, -702000000, -702010000})

# access_token 提前多少秒视为需要续期（与 session._ensure_auth 一致）
TOKEN_REFRESH_SKEW_SEC = 60


def data_dir(home: Path | str | None = None) -> Path:
    if home is not None:
        root = Path(home)
    else:
        root = Path(os.environ.get("MIOT_CTL_HOME", Path.cwd() / "data"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def config_path(home: Path | str | None = None) -> Path:
    return data_dir(home) / "config.json"


def auth_path(home: Path | str | None = None) -> Path:
    return data_dir(home) / "auth.json"


def meta_path(home: Path | str | None = None) -> Path:
    return data_dir(home) / "meta.json"


def homes_path(home: Path | str | None = None) -> Path:
    return data_dir(home) / "homes.json"


def load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def lite_iid_to_api(iid: str) -> str:
    """prop.0.2.1 -> prop.2.1 ; action.0.3.1 -> action.3.1"""
    parts = iid.split(".")
    if len(parts) == 4 and parts[1] == "0" and parts[0] in {"prop", "action"}:
        return f"{parts[0]}.{parts[2]}.{parts[3]}"
    return iid


def api_iid_to_parts(iid: str) -> tuple[str, int, int]:
    parts = iid.split(".")
    if len(parts) != 3 or parts[0] not in {"prop", "action"}:
        raise ValueError(
            f"无效 iid: {iid}，期望 prop.{{siid}}.{{piid}} 或 action.{{siid}}.{{aiid}}"
        )
    return parts[0], int(parts[1]), int(parts[2])


def infer_value(raw: str) -> Any:
    s = raw.strip()
    low = s.lower()
    if low in {"true", "on", "yes", "1"}:
        return True
    if low in {"false", "off", "no", "0"}:
        return False
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+\.\d+", s):
        return float(s)
    return s


def code_msg(code: Any) -> str | None:
    if not isinstance(code, int) or code in MIOT_OK_CODES or code >= 0:
        return None
    return MIOT_SPEC_CODES.get(code, f"设备侧错误码 {code}")


def resolve_spec_key(
    spec: dict[str, MIoTSpecDeviceLite], key: str, *, writable: bool | None = None
) -> MIoTSpecDeviceLite:
    if key in spec:
        item = spec[key]
        if writable is not None and item.writeable != writable:
            raise ValueError(f"'{key}' 不可写")
        return item

    if key.startswith(("prop.", "action.")):
        api = lite_iid_to_api(key) if key.count(".") == 3 and ".0." in key else key
        for item in spec.values():
            if lite_iid_to_api(item.iid) == api:
                if writable is not None and item.writeable != writable:
                    raise ValueError(f"'{key}' 不可写")
                return item
        raise ValueError(f"设备 spec 中未找到 iid: {key}")

    matches = [
        item
        for item in spec.values()
        if item.type_name == key and (writable is None or item.writeable == writable)
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = ", ".join(sorted({m.iid for m in matches}))
        raise ValueError(f"'{key}' 有多个匹配，请用更具体的 iid: {names}")
    raise ValueError(f"设备 spec 中未找到: {key}")


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
