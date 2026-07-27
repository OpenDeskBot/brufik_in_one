"""Flask 风格测试客户端包装（基于 Starlette TestClient）。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient


class _Resp:
    def __init__(self, resp):
        self._resp = resp
        self.status_code = resp.status_code
        self.headers = resp.headers
        self.data = resp.content
        self.content = resp.content
        self.text = resp.text

    def get_data(self, as_text: bool = False) -> bytes | str:
        if as_text:
            return self.text
        return self.data

    @property
    def is_json(self) -> bool:
        ctype = (self.headers.get("content-type") or "").lower()
        return "application/json" in ctype

    def get_json(self) -> Any:
        return self._resp.json()

    def json(self) -> Any:
        return self._resp.json()


def _normalize_kwargs(kwargs: dict) -> dict:
    """Flask ``content_type=`` → httpx ``headers``。"""
    out = dict(kwargs)
    ctype = out.pop("content_type", None)
    # multipart 由 files= 自动设置 Content-Type；勿手动强制
    if ctype and "multipart/form-data" not in str(ctype).lower():
        headers = dict(out.get("headers") or {})
        headers.setdefault("Content-Type", ctype)
        out["headers"] = headers
    return out


def _split_flask_multipart(data) -> tuple[dict | None, dict | None]:
    """Flask ``data={field: (BytesIO, filename)}`` → httpx ``data`` + ``files``。"""
    if not isinstance(data, dict):
        return data, None
    form: dict = {}
    files: dict = {}
    for key, val in data.items():
        if isinstance(val, (tuple, list)) and val and hasattr(val[0], "read"):
            fileobj = val[0]
            filename = val[1] if len(val) > 1 else key
            content_type = val[2] if len(val) > 2 else None
            if content_type:
                files[key] = (filename, fileobj, content_type)
            else:
                files[key] = (filename, fileobj)
        else:
            form[key] = val
    return (form or None), (files or None)


class WebTestClient:
    """默认不跟随重定向（与 Flask ``test_client`` 一致）。"""

    def __init__(self, app: FastAPI):
        self._client = TestClient(app)

    def get(self, url: str, *, follow_redirects: bool = False, **kwargs) -> _Resp:
        return _Resp(self._client.get(url, follow_redirects=follow_redirects, **_normalize_kwargs(kwargs)))

    def post(self, url: str, *, data=None, json=None, follow_redirects: bool = False, **kwargs) -> _Resp:
        kw = _normalize_kwargs(kwargs)
        form, files = _split_flask_multipart(data)
        if files is not None:
            return _Resp(
                self._client.post(url, data=form, files=files, json=json, follow_redirects=follow_redirects, **kw)
            )
        return _Resp(
            self._client.post(url, data=data, json=json, follow_redirects=follow_redirects, **kw)
        )

    def put(self, url: str, *, data=None, json=None, follow_redirects: bool = False, **kwargs) -> _Resp:
        return _Resp(
            self._client.put(
                url, data=data, json=json, follow_redirects=follow_redirects, **_normalize_kwargs(kwargs)
            )
        )

    def patch(self, url: str, *, data=None, json=None, follow_redirects: bool = False, **kwargs) -> _Resp:
        return _Resp(
            self._client.patch(
                url, data=data, json=json, follow_redirects=follow_redirects, **_normalize_kwargs(kwargs)
            )
        )

    def delete(self, url: str, *, follow_redirects: bool = False, **kwargs) -> _Resp:
        return _Resp(self._client.delete(url, follow_redirects=follow_redirects, **_normalize_kwargs(kwargs)))
