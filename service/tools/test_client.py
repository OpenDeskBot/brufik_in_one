#!/usr/bin/env python3
import argparse
import asyncio
import base64
import io
import json
import wave

import websockets


def pcm_s16le_to_wav_bytes(pcm: bytes, sample_rate: int, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buf.getvalue()


def load_wav_pcm(path: str) -> bytes:
    with wave.open(path, "rb") as wf:
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        sample_rate = wf.getframerate()
        if channels != 1 or sample_width != 2 or sample_rate != 16000:
            raise ValueError(
                "输入 wav 必须是 16kHz / 单声道 / 16-bit PCM。"
                f"当前: {sample_rate}Hz, {channels}ch, {sample_width * 8}bit"
            )
        return wf.readframes(wf.getnframes())


async def recv_loop(ws, output_tts: str):
    pcm_chunks: list[bytes] = []
    sample_rate = 24000
    while True:
        msg = await ws.recv()
        if isinstance(msg, bytes):
            pcm_chunks.append(msg)
            print(f"[tts] 收到 PCM 分片 {len(msg)} bytes（累计 {sum(len(x) for x in pcm_chunks)}）")
            continue

        data = json.loads(msg)
        msg_type = data.get("type")
        print(f"[server] {data}")
        if msg_type in ("pb_start", "pb_chunk", "pb_end", "pb_single") and data.get("sr"):
            sample_rate = int(data["sr"])
        if msg_type in ("pb_end", "pb_single"):
            if not pcm_chunks:
                print("[warn] 收到 pb_end/pb_single 但未收到二进制 PCM")
            else:
                raw = b"".join(pcm_chunks)
                wav_b = pcm_s16le_to_wav_bytes(raw, sample_rate)
                with open(output_tts, "wb") as f:
                    f.write(wav_b)
                print(
                    f"[tts] 已合并 {len(pcm_chunks)} 段 PCM → WAV: {output_tts} "
                    f"（{len(wav_b)} bytes, sr={sample_rate}）"
                )
            break


async def run(args):
    pcm = load_wav_pcm(args.input_wav)
    frame_bytes = int(16000 * (args.frame_ms / 1000.0) * 2)

    async with websockets.connect(args.ws_url, max_size=None) as ws:
        ready = await ws.recv()
        print(f"[server] {ready}")

        recv_task = asyncio.create_task(recv_loop(ws, args.output_tts))

        for i in range(0, len(pcm), frame_bytes):
            frame = pcm[i : i + frame_bytes]
            if not frame:
                continue
            payload = {
                "type": "audio",
                "codec": "pcm16",
                "data": base64.b64encode(frame).decode("ascii"),
            }
            await ws.send(json.dumps(payload))
            await asyncio.sleep(args.push_interval_ms / 1000.0)

        await ws.send(json.dumps({"type": "flush"}))
        await recv_task


def main():
    parser = argparse.ArgumentParser(description="deskbot-server WebSocket 测试客户端")
    parser.add_argument(
        "--ws-url",
        default="ws://127.0.0.1:9000/asr_chat",
        help="deskbot-server WebSocket 地址",
    )
    parser.add_argument(
        "--input-wav",
        required=True,
        help="输入 wav（16kHz/mono/16bit）",
    )
    parser.add_argument(
        "--output-tts",
        default="tts_reply.wav",
        help="将 pb 下发的 s16le PCM 合并后保存为 WAV 的路径",
    )
    parser.add_argument(
        "--frame-ms",
        type=int,
        default=30,
        help="发送帧长（毫秒），默认 30ms",
    )
    parser.add_argument(
        "--push-interval-ms",
        type=int,
        default=20,
        help="推流间隔（毫秒），默认 20ms",
    )
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
