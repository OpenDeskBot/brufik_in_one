#!/usr/bin/env python3
"""deskbot-server 摄像头烟囱客户端：经 ``/asr_chat`` 发送 ``camera_frame`` + JPEG。

用法示例：

    python camera_test_client.py --image photo.jpg --device-id deskbot_dev --fps 5 --frames 30
    python camera_test_client.py --image-dir ./frames --device-id deskbot_dev --fps 8

依赖：``websockets``（项目已有）。
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import List

import websockets

SUPPORTED_EXT = (".jpg", ".jpeg", ".png")


def _list_images(image: str | None, image_dir: str | None) -> List[Path]:
    if image:
        p = Path(image)
        if not p.is_file():
            raise FileNotFoundError(f"图片不存在: {p}")
        return [p]
    if image_dir:
        d = Path(image_dir)
        if not d.is_dir():
            raise FileNotFoundError(f"目录不存在: {d}")
        items = sorted(
            f for f in d.iterdir() if f.is_file() and f.suffix.lower() in SUPPORTED_EXT
        )
        if not items:
            raise FileNotFoundError(f"目录里没有 {SUPPORTED_EXT} 文件: {d}")
        return items
    raise ValueError("--image 或 --image-dir 至少要给一个")


def _asr_chat_url(base: str, device_id: str) -> str:
    base = base.rstrip("/")
    if "?" in base:
        return f"{base}&device_id={device_id}"
    return f"{base}?device_id={device_id}"


async def _recv_loop(ws):
    try:
        async for msg in ws:
            if isinstance(msg, (bytes, bytearray)):
                continue
            try:
                d = json.loads(msg)
            except Exception:
                print(f"[server] (non-json) {msg!r}")
                continue
            t = d.get("type")
            if t in ("ready", "error"):
                print(f"[server] {d}")
            elif t and not str(t).startswith("pb_"):
                print(f"[server] {d}")
    except websockets.exceptions.ConnectionClosed as e:
        print(f"[server] connection closed: {e}")


async def run(args):
    images = _list_images(args.image, args.image_dir)
    print(
        f"准备推 {args.frames} 帧 @ {args.fps} fps，"
        f"循环 {len(images)} 张图：{[p.name for p in images[:5]]}"
        + (" ..." if len(images) > 5 else "")
    )

    interval = 1.0 / max(0.1, args.fps)
    url = _asr_chat_url(args.ws_url, args.device_id)

    async with websockets.connect(url, max_size=None) as ws:
        recv_task = asyncio.create_task(_recv_loop(ws))
        try:
            for i in range(args.frames):
                p = images[i % len(images)]
                buf = p.read_bytes()
                header = json.dumps(
                    {
                        "type": "camera_frame",
                        "codec": "jpeg",
                        "next_bin_len": len(buf),
                        "seq": i + 1,
                    }
                )
                t0 = time.monotonic()
                await ws.send(header)
                await ws.send(buf)
                rt = (time.monotonic() - t0) * 1000.0
                print(
                    f"[push] frame={i + 1:>4} file={p.name} "
                    f"size={len(buf)} send_ms={rt:.1f}"
                )
                await asyncio.sleep(interval)
            await asyncio.sleep(0.5)
        finally:
            recv_task.cancel()
            try:
                await recv_task
            except asyncio.CancelledError:
                pass


def main():
    parser = argparse.ArgumentParser(
        description="经 /asr_chat 推 camera_frame+JPEG，供人脸检测与 /camera_view 预览",
    )
    parser.add_argument(
        "--ws-url",
        default="ws://127.0.0.1:9000/asr_chat",
        help="deskbot-server /asr_chat WebSocket 地址（不含 query）",
    )
    parser.add_argument("--device-id", required=True, help="URL ?device_id=")
    parser.add_argument("--image", help="单张 JPEG/PNG")
    parser.add_argument("--image-dir", help="目录，按字典序循环")
    parser.add_argument("--frames", type=int, default=10)
    parser.add_argument("--fps", type=float, default=5.0)
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
