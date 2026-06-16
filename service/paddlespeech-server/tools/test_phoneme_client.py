#!/usr/bin/env python3
"""最小 streaming_phoneme 联调客户端。

用法（需 TTS 服务已启动）::

    python tools/test_phoneme_client.py --text "你好"
    python tools/test_phoneme_client.py --url ws://127.0.0.1:8092/paddlespeech/tts/streaming_phoneme
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import wave
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


async def run(url: str, text: str, spk_id: int, out_wav: Path | None) -> int:
    segments: list[dict] = []
    async with websockets.connect(url, max_size=None, open_timeout=60) as ws:
        await ws.send(json.dumps({"signal": "start"}))
        hello = json.loads(await ws.recv())
        if hello.get("status") != 0:
            print("handshake failed:", hello, file=sys.stderr)
            return 1
        session = hello.get("session")
        print(f"session={session}")

        await ws.send(json.dumps({"text": text, "spk_id": spk_id}))
        while True:
            pkt = json.loads(await ws.recv())
            st = pkt.get("status")
            if st == -1:
                print("error:", pkt, file=sys.stderr)
                return 1
            if st == 1:
                segments = pkt.get("segments") or []
                print(f"segments={len(segments)}")
                for i, seg in enumerate(segments[:5]):
                    print(
                        f"  [{i}] phoneme={seg.get('phoneme')!r} "
                        f"id={seg.get('phoneme_id')} ms={seg.get('ms')}"
                    )
                if len(segments) > 5:
                    print(f"  ... ({len(segments) - 5} more)")
                continue
            if st == 2:
                break

        await ws.send(json.dumps({"signal": "end", "session": session}))
        try:
            await asyncio.wait_for(ws.recv(), timeout=5.0)
        except (asyncio.TimeoutError, Exception):
            pass

    pcm = b"".join(
        __import__("base64").b64decode(s.get("audio") or "") for s in segments
    )
    print(f"total_pcm_bytes={len(pcm)}")

    if out_wav and pcm:
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out_wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(pcm)
        print(f"wrote {out_wav}")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Test streaming_phoneme WebSocket")
    parser.add_argument(
        "--url",
        default="ws://127.0.0.1:8092/paddlespeech/tts/streaming_phoneme",
    )
    parser.add_argument("--text", default="你好，我是桌面机器人。")
    parser.add_argument("--spk-id", type=int, default=0)
    parser.add_argument(
        "--out-wav",
        type=Path,
        default=Path("out_phoneme_test.wav"),
        help="写入拼接后的 PCM WAV；设为空字符串跳过",
    )
    args = parser.parse_args()
    out = args.out_wav if str(args.out_wav) else None
    raise SystemExit(asyncio.run(run(args.url, args.text, args.spk_id, out)))


if __name__ == "__main__":
    main()
