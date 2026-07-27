"""endpoint 注册、url_for 与 flash 消息。"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode

from fastapi import Request

FLASH_KEY = "_flashes"

_endpoint_map: dict[str, str] = {}


def register_endpoint(name: str, path: str) -> None:
    _endpoint_map[name] = path


def endpoint_path(name: str) -> str | None:
    return _endpoint_map.get(name)


def flash(request: Request, message: str, category: str = "message") -> None:
    flashes = list(request.session.get(FLASH_KEY) or [])
    flashes.append((category, message))
    request.session[FLASH_KEY] = flashes


def get_flashed_messages(request: Request, with_categories: bool = False):
    flashes = list(request.session.pop(FLASH_KEY, []) or [])
    if with_categories:
        return flashes
    return [m for _c, m in flashes]


def url_for(endpoint: str, **values: Any) -> str:
    if endpoint == "static":
        filename = values.get("filename") or ""
        return f"/static/{filename.lstrip('/')}"
    path = _endpoint_map.get(endpoint)
    if path is None:
        aliases = {
            "auth.login": "/login",
            "auth.register": "/register",
            "auth.logout": "/logout",
            "site.index": "/",
            "app2c.home": "/home",
        }
        path = aliases.get(endpoint, f"/{endpoint.replace('.', '/')}")
    for key, val in list(values.items()):
        token = "{" + key + "}"
        if token in path:
            path = path.replace(token, str(val))
            values.pop(key, None)
    for key, val in list(values.items()):
        path2, n = re.subn(rf"<{key}([^>]*)>", str(val), path)
        if n:
            path = path2
            values.pop(key, None)
    query = {k: v for k, v in values.items() if v is not None and k not in ("_external",)}
    if query:
        return f"{path}?{urlencode(query, doseq=True)}"
    return path
