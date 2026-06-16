"""PaddleSpeech 音素对齐 TTS WebSocket（与 web.app 共用逻辑）。"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import websockets


def phoneme_streaming_url_from_tts_ws(ws_url: str) -> str:
    """由流式 TTS 的 ``ws_url`` 推导 ``/paddlespeech/tts/streaming_phoneme`` 地址。"""
    raw = str(ws_url or "").strip()
    if "://" not in raw:
        raw = "ws://" + raw
    scheme, rest = raw.split("://", 1)
    slash = rest.find("/")
    base = f"{scheme}://{rest[:slash]}" if slash != -1 else f"{scheme}://{rest}"
    return f"{base}/paddlespeech/tts/streaming_phoneme"


async def fetch_phoneme_tts(
    ws_url: str, text: str, spk_id: int
) -> tuple[list[dict[str, Any]], bytes]:
    """连接 ``streaming_phoneme``，返回 (按片元数据列表, 拼接后的整段 PCM)。

    每个片 dict: ``phoneme``, ``ms``, ``pcm`` (bytes), 可选 ``phoneme_id``。
    """
    segments: list[dict[str, Any]] = []
    async with websockets.connect(ws_url, max_size=None, open_timeout=60) as ws:
        await ws.send(json.dumps({"task": "tts", "signal": "start"}))
        r0 = json.loads(await ws.recv())
        if r0.get("status") != 0:
            raise RuntimeError(f"PaddleSpeech phoneme 握手失败: {r0}")
        session = r0.get("session")
        await ws.send(json.dumps({"text": text, "spk_id": spk_id}))

        while True:
            pkt = json.loads(await ws.recv())
            st = pkt.get("status")
            if st == -1:
                raise RuntimeError(str(pkt.get("message") or pkt))
            if st == 1 and isinstance(pkt.get("segments"), list):
                raw_segs = pkt["segments"]
                segments = []
                for s in raw_segs:
                    b64 = s.get("audio") or ""
                    pcm = base64.b64decode(b64) if b64 else b""
                    segments.append(
                        {
                            "phoneme_id": s.get("phoneme_id"),
                            "phoneme": s.get("phoneme"),
                            "ms": int(s.get("ms") or 0),
                            "pcm": pcm,
                        }
                    )
                continue
            if st == 2:
                break

        await ws.send(
            json.dumps({"task": "tts", "signal": "end", "session": session})
        )
        try:
            await asyncio.wait_for(ws.recv(), timeout=5.0)
        except (asyncio.TimeoutError, Exception):
            pass

    full = b"".join(s["pcm"] for s in segments)
    return segments, full
