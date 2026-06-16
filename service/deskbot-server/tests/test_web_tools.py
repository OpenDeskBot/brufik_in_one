from __future__ import annotations

from unittest.mock import patch


def test_webfetch_ok():
    from deskbot_server.web_tools import webfetch

    class _Resp:
        status = 200
        headers = {"Content-Type": "text/plain"}

        def read(self, n=-1):
            return b"hello"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch("deskbot_server.web_tools.urllib.request.urlopen", return_value=_Resp()):
        out = webfetch("https://example.com")
    assert out["ok"] is True
    assert "hello" in out["text"]


def test_websearch_returns_structure():
    from deskbot_server.web_tools import websearch

    payload = '{"AbstractText":"测试摘要","Heading":"标题","RelatedTopics":[]}'.encode()
    with patch(
        "deskbot_server.web_tools._http_get",
        return_value=(200, "application/json", payload),
    ):
        out = websearch("测试")
    assert out["ok"] is True
    assert out["results"]
