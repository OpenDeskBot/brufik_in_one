"""WebSocket 请求解析：路径拆分、查询参数提取、设备/PIN 识别。"""

from __future__ import annotations

from urllib.parse import unquote_plus


def ws_request_path(websocket) -> str:
    """从 WebSocket 对象提取请求路径。"""
    req_path = getattr(websocket, "path", None)
    if req_path is None:
        req_path = getattr(getattr(websocket, "request", None), "path", None)
    return req_path or ""


def split_path(raw_path: str) -> tuple[str, str]:
    """把 ``/face_pos?role=subscriber`` 拆成 ``(path, query)``。"""
    if not raw_path:
        return "", ""
    if "?" in raw_path:
        path, _, query = raw_path.partition("?")
        return path, query
    return raw_path, ""


def parse_query(query: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if not query:
        return out
    for part in query.split("&"):
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
        else:
            k, v = part, ""
        out[k.strip().lower()] = unquote_plus(v.strip(), encoding="utf-8", errors="replace")
    return out


def extract_device_id(qargs: dict) -> str | None:
    """从 URL 查询参数里按兼容顺序取 device_id。

    支持的别名：``device_id`` / ``deviceid`` / ``device`` / ``id``，均大小写不敏感。
    返回已 strip 的字符串，若为空返回 None。
    """
    for key in ("device_id", "deviceid", "device", "id"):
        v = qargs.get(key)
        if v:
            v = str(v).strip()
            if v:
                return v
    return None


def extract_pin_code(qargs: dict) -> str | None:
    """从 URL 查询参数取 pin_code（别名 ``pin`` / ``pincode``）。"""
    for key in ("pin_code", "pincode", "pin"):
        v = qargs.get(key)
        if v:
            v = str(v).strip()
            if v:
                return v
    return None
