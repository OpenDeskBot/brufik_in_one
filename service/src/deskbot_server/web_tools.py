"""LLM 网络工具：webfetch / websearch。"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html import unescape
from typing import Any

_USER_AGENT = "OpenDesk-Deskbot/1.0"
_MAX_FETCH_BYTES = 120_000
_FETCH_TIMEOUT_SEC = 20
_MAX_SEARCH_RESULTS = 8

_DDG_TOPIC_RE = re.compile(
    r'<a class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
    r'.*?<a class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)


def _http_get(url: str, *, timeout: int = _FETCH_TIMEOUT_SEC, max_bytes: int = _MAX_FETCH_BYTES) -> tuple[int, str, bytes]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept": "text/html,application/json,*/*"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = int(getattr(resp, "status", 200) or 200)
        chunks: list[bytes] = []
        total = 0
        while True:
            block = resp.read(min(8192, max_bytes - total))
            if not block:
                break
            chunks.append(block)
            total += len(block)
            if total >= max_bytes:
                break
        body = b"".join(chunks)
    return status, str(resp.headers.get("Content-Type") or ""), body


def webfetch(url: str) -> dict[str, Any]:
    raw = str(url or "").strip()
    if not raw:
        raise ValueError("url 不能为空")
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("仅支持 http/https URL")
    if not parsed.netloc:
        raise ValueError("url 无效")
    try:
        status, content_type, body = _http_get(raw)
    except urllib.error.HTTPError as exc:
        err_body = exc.read(_MAX_FETCH_BYTES) if exc.fp else b""
        text = err_body.decode("utf-8", errors="replace")[:8000]
        return {
            "ok": False,
            "url": raw,
            "status": int(exc.code),
            "content_type": str(exc.headers.get("Content-Type") or ""),
            "error": str(exc.reason),
            "text": text,
        }
    except urllib.error.URLError as exc:
        return {"ok": False, "url": raw, "error": str(exc.reason)}
    text = body.decode("utf-8", errors="replace")
    if len(body) >= _MAX_FETCH_BYTES:
        text += "\n…(内容已截断)"
    return {
        "ok": True,
        "url": raw,
        "status": status,
        "content_type": content_type,
        "bytes": len(body),
        "text": text[:12000],
    }


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    return unescape(re.sub(r"\s+", " ", text)).strip()


def websearch(query: str, *, max_results: int = _MAX_SEARCH_RESULTS) -> dict[str, Any]:
    q = str(query or "").strip()
    if not q:
        raise ValueError("query 不能为空")
    limit = max(1, min(int(max_results), _MAX_SEARCH_RESULTS))

    # DuckDuckGo Instant Answer API（无密钥）
    api_url = "https://api.duckduckgo.com/?" + urllib.parse.urlencode(
        {"q": q, "format": "json", "no_html": 1, "skip_disambig": 1}
    )
    results: list[dict[str, str]] = []
    abstract = ""
    try:
        status, _ct, body = _http_get(api_url, max_bytes=64_000)
        if status == 200:
            data = json.loads(body.decode("utf-8", errors="replace"))
            abstract = str(data.get("AbstractText") or "").strip()
            if abstract:
                results.append(
                    {
                        "title": str(data.get("Heading") or "摘要"),
                        "url": str(data.get("AbstractURL") or ""),
                        "snippet": abstract,
                    }
                )
            for topic in data.get("RelatedTopics") or []:
                if len(results) >= limit:
                    break
                if isinstance(topic, dict) and topic.get("Text"):
                    results.append(
                        {
                            "title": str(topic.get("Text") or "")[:120],
                            "url": str(topic.get("FirstURL") or ""),
                            "snippet": str(topic.get("Text") or "")[:400],
                        }
                    )
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        pass

    # HTML 备用检索
    if len(results) < limit:
        html_url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": q})
        try:
            _status, _ct, body = _http_get(html_url, max_bytes=200_000)
            html = body.decode("utf-8", errors="replace")
            for m in _DDG_TOPIC_RE.finditer(html):
                if len(results) >= limit:
                    break
                href = unescape(m.group(1))
                title = _strip_html(m.group(2))
                snippet = _strip_html(m.group(3))
                if title:
                    results.append({"title": title, "url": href, "snippet": snippet})
        except (urllib.error.URLError, OSError):
            pass

    return {"ok": True, "query": q, "results": results[:limit], "abstract": abstract or None}
