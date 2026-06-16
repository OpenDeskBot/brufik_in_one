from __future__ import annotations

import asyncio
import json

import websockets
from websockets.datastructures import Headers
from websockets.http11 import Response
from websockets.streams import StreamReader

from deskbot_server.ws.http11_compat import (
    parse_http_request_with_body,
    patch_websockets_http11_for_rest_api,
)


def _drive(reader: StreamReader, gen):
    while True:
        try:
            next(gen)
        except StopIteration as exc:
            return exc.value


def test_parse_post_json_body():
    payload = {"device_id": "d1", "playbook": {"name": "demo"}}
    body = json.dumps(payload).encode("utf-8")
    raw = (
        b"POST /api/scene_playbook/run HTTP/1.1\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: "
        + str(len(body)).encode()
        + b"\r\n\r\n"
        + body
    )
    reader = StreamReader()
    reader.feed_data(raw)
    req = _drive(
        reader,
        parse_http_request_with_body(reader.read_line, reader.read_exact),
    )
    assert getattr(req, "method", None) == "POST"
    assert req.path == "/api/scene_playbook/run"
    assert json.loads(getattr(req, "body", b"").decode()) == payload


def test_parse_get_without_body():
    raw = b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n"
    reader = StreamReader()
    reader.feed_data(raw)
    req = _drive(
        reader,
        parse_http_request_with_body(reader.read_line, reader.read_exact),
    )
    assert getattr(req, "method", None) == "GET"
    assert req.path == "/health"
    assert getattr(req, "body", b"") == b""


def test_websockets_serve_accepts_post():
    def make_resp(body: bytes = b"ok") -> Response:
        headers = Headers()
        headers["Content-Length"] = str(len(body))
        return Response(200, "OK", headers, body)

    async def run():
        patch_websockets_http11_for_rest_api()

        def process_request(_conn, request):
            if getattr(request, "method", "GET") == "POST":
                return make_resp()
            return make_resp()

        async with websockets.serve(
            lambda ws: None,
            "127.0.0.1",
            0,
            process_request=process_request,
        ) as server:
            port = server.sockets[0].getsockname()[1]
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"POST /test HTTP/1.1\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            resp = await asyncio.wait_for(reader.read(1024), timeout=2)
            writer.close()
            assert b"200" in resp.split(b"\r\n", 1)[0]

    asyncio.run(run())
