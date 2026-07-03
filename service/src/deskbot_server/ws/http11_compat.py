"""websockets 14 的 Request.parse 仅支持无 body 的 GET；Deskbot 在同端口提供 REST API 需 POST/PUT。"""

from __future__ import annotations

from collections.abc import Generator
from typing import Callable

from websockets.exceptions import InvalidMessage
from websockets.http11 import Request, d, parse_headers, parse_line, read_body
from websockets.protocol import CONNECTING, Protocol
from websockets.server import ServerProtocol

_ALLOWED_METHODS = frozenset(
    {b"GET", b"POST", b"PUT", b"DELETE", b"OPTIONS", b"HEAD"}
)

_protocol_base_parse = Protocol.parse


def parse_http_request_with_body(
    read_line: Callable[[int], Generator[None, None, bytes]],
    read_exact: Callable[[int], Generator[None, None, bytes]],
) -> Generator[None, None, Request]:
    """解析 HTTP/1.1 请求行、头与 Content-Length body（与 websockets Request.parse 兼容）。"""
    try:
        request_line = yield from parse_line(read_line)
    except EOFError as exc:
        raise EOFError("connection closed while reading HTTP request line") from exc

    try:
        method_b, raw_path, protocol = request_line.split(b" ", 2)
    except ValueError:
        raise ValueError(f"invalid HTTP request line: {d(request_line)}") from None
    method_b = bytes(method_b)
    protocol = bytes(protocol)
    raw_path = bytes(raw_path)
    if protocol != b"HTTP/1.1":
        raise ValueError(
            f"unsupported protocol; expected HTTP/1.1: {d(request_line)}"
        )
    if method_b not in _ALLOWED_METHODS:
        raise ValueError(
            f"unsupported HTTP method; expected GET/POST/...; got {d(method_b)}"
        )

    path = raw_path.decode("ascii", "surrogateescape")
    headers = yield from parse_headers(read_line)

    body = b""
    if "Transfer-Encoding" in headers:
        raise NotImplementedError("transfer codings aren't supported")

    if "Content-Length" in headers:
        body = yield from read_body(
            200,
            headers,
            read_line,
            read_exact,
            read_line,
        )

    req = Request(path, headers)
    object.__setattr__(req, "method", method_b.decode("ascii"))
    object.__setattr__(req, "body", body)
    return req


def _patched_server_protocol_parse(self) -> Generator[None]:
    if self.state is CONNECTING:
        try:
            request = yield from parse_http_request_with_body(
                self.reader.read_line,
                self.reader.read_exact,
            )
        except Exception as exc:
            self.handshake_exc = InvalidMessage(
                "did not receive a valid HTTP request"
            )
            self.handshake_exc.__cause__ = exc
            self.send_eof()
            self.parser = self.discard()
            next(self.parser)
            yield
            return

        if self.debug:
            method = getattr(request, "method", "GET")
            self.logger.debug("< %s %s HTTP/1.1", method, request.path)
            for key, value in request.headers.raw_items():
                self.logger.debug("< %s: %s", key, value)
            body = getattr(request, "body", b"")
            if body:
                self.logger.debug("< [body] (%d bytes)", len(body))

        self.events.append(request)

    yield from _protocol_base_parse(self)


def patch_websockets_http11_for_rest_api() -> None:
    """在 websockets.serve 之前调用一次，使 process_request 能收到 POST body。"""
    if ServerProtocol.parse is _patched_server_protocol_parse:
        return
    ServerProtocol.parse = _patched_server_protocol_parse  # type: ignore[method-assign]
