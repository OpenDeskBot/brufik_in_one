#!/usr/bin/env python3
import argparse
import asyncio
import base64
import io
import json
import signal
import sys
import time
import wave

import numpy as np
import websockets

try:
    import sounddevice as sd
except OSError as e:
    print(f"无法加载 sounddevice: {e}")
    print("请先安装 PortAudio 系统库，例如：sudo apt-get install -y libportaudio2 portaudio19-dev")
    sys.exit(1)


def parse_args():
    parser = argparse.ArgumentParser(description="deskbot-server 实时麦克风测试客户端")
    parser.add_argument("--ws-url", default="ws://127.0.0.1:9000/asr_chat", help="deskbot-server WebSocket 地址")
    parser.add_argument("--sample-rate", type=int, default=16000, help="麦克风采样率，默认 16000")
    parser.add_argument("--channels", type=int, default=1, help="麦克风声道数，默认 1")
    parser.add_argument("--frame-ms", type=int, default=30, help="发送帧长（毫秒），默认 30")
    parser.add_argument("--device", type=int, default=None, help="输入设备 ID（可选）")
    parser.add_argument(
        "--play-device",
        type=int,
        default=None,
        help="输出设备 ID（可选；默认用系统默认输出。若听筒/HDMI/蓝牙混用，建议显式指定）",
    )
    parser.add_argument(
        "--play-gain",
        type=float,
        default=1.0,
        help="TTS 播放增益（对 float32 生效），默认 1.0",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="列出 sounddevice 设备后退出",
    )
    return parser.parse_args()


async def sender_loop(ws, audio_queue: asyncio.Queue, stop_event: asyncio.Event):
    while not stop_event.is_set():
        try:
            chunk = await asyncio.wait_for(audio_queue.get(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        payload = {
            "type": "audio",
            "codec": "pcm16",
            "data": base64.b64encode(chunk).decode("ascii"),
        }
        await ws.send(json.dumps(payload))


async def ping_loop(ws, stop_event: asyncio.Event):
    while not stop_event.is_set():
        await asyncio.sleep(10)
        try:
            await ws.send(json.dumps({"type": "ping"}))
        except Exception:
            return


def _resolve_play_device(requested: int | None) -> int | None:
    """选择可用的输出设备。部分机器上默认/指定设备可能 max_output_channels=0，会直接导致 sd.play 失败。"""
    try:
        devices = sd.query_devices()
    except Exception:
        return requested

    def out_ch(i: int) -> int:
        try:
            return int(devices[i].get("max_output_channels", 0) or 0)
        except Exception:
            return 0

    if requested is not None:
        if 0 <= requested < len(devices) and out_ch(requested) > 0:
            return requested
        # 回退到第一个有输出通道的设备
        for i, d in enumerate(devices):
            if int(d.get("max_output_channels", 0) or 0) > 0:
                print(
                    f"[audio] 警告: 输出设备 {requested!s} 不可用或 max_output_channels=0，"
                    f"已回退到 {i} {d.get('name', '')!r}"
                )
                return i
        return requested

    # requested 为空：尽量确保默认输出设备有输出通道
    try:
        out_dev = sd.default.device[1]
    except Exception:
        out_dev = None
    if out_dev is not None and 0 <= out_dev < len(devices) and out_ch(out_dev) > 0:
        return None
    for i, d in enumerate(devices):
        if int(d.get("max_output_channels", 0) or 0) > 0:
            print(
                f"[audio] 警告: 系统默认输出设备 {out_dev!s} 不可用，已回退到 {i} {d.get('name', '')!r}"
            )
            return i
    return None


def pcm_s16le_mono_to_wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def play_wav_bytes(
    wav_bytes: bytes, *, play_device: int | None, play_gain: float
) -> dict:
    dev = _resolve_play_device(play_device)
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        channels = wf.getnchannels()
        sample_rate = wf.getframerate()
        sample_width = wf.getsampwidth()
        nframes = wf.getnframes()
        audio = wf.readframes(nframes)

    if sample_width != 2:
        raise ValueError(f"仅支持 16-bit PCM WAV 播放，当前 sample_width={sample_width}")

    pcm = np.frombuffer(audio, dtype=np.int16)
    if channels > 1:
        pcm = pcm.reshape(-1, channels)
    else:
        pcm = pcm.reshape(-1, 1)

    f32 = (pcm.astype(np.float32) / 32768.0) * float(play_gain)
    peak = float(np.max(np.abs(f32))) if f32.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(f32)))) if f32.size else 0.0
    if peak < 1e-6:
        print(f"[playback] 警告: 音频几乎全为静音 peak={peak:.6f} rms={rms:.6f}（检查 TTS/服务端输出）")

    # sounddevice：单声道建议用 1D 数组；不要使用容易触发驱动 bug 的 mapping=...
    if channels == 1:
        out_audio = f32[:, 0]
    else:
        out_audio = f32

    sd.play(out_audio, samplerate=sample_rate, device=dev)
    sd.wait()
    return {
        "bytes": len(wav_bytes),
        "sample_rate": int(sample_rate),
        "channels": int(channels),
        "frames": int(nframes),
        "peak": peak,
        "rms": rms,
    }


async def tts_player_loop(
    tts_queue: asyncio.Queue, stop_event: asyncio.Event, *, play_device: int | None, play_gain: float
):
    while not stop_event.is_set():
        try:
            wav_bytes = await asyncio.wait_for(tts_queue.get(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        try:
            info = await asyncio.to_thread(
                play_wav_bytes, wav_bytes, play_device=play_device, play_gain=play_gain
            )
            print(
                f"[playback] 已播放 TTS: bytes={info['bytes']}, {info['sample_rate']}Hz, ch={info['channels']}, "
                f"frames={info['frames']}, peak={info['peak']:.4f}, rms={info['rms']:.4f}"
            )
        except Exception as e:
            print(f"[playback] 播放失败: {e}")


async def receiver_loop(ws, tts_queue: asyncio.Queue, stop_event: asyncio.Event):
    pcm_buf = bytearray()
    tts_sr = 24000
    while not stop_event.is_set():
        msg = await ws.recv()
        if isinstance(msg, bytes):
            pcm_buf.extend(msg)
            print(f"[tts] 收到 PCM 分片 {len(msg)} bytes（本轮累计 {len(pcm_buf)}）")
            continue

        data = json.loads(msg)
        msg_type = data.get("type")
        if msg_type in ("pb_start", "pb_chunk", "pb_end", "pb_single") and data.get("sr"):
            tts_sr = int(data["sr"])
        if msg_type in ("pb_end", "pb_single"):
            if pcm_buf:
                wav_b = pcm_s16le_mono_to_wav_bytes(bytes(pcm_buf), tts_sr)
                pcm_buf.clear()
                print(f"[tts] 本轮合并 PCM → WAV {len(wav_b)} bytes（sr={tts_sr}），排队播放")
                await tts_queue.put(wav_b)
            else:
                print("[tts] pb_end/pb_single 但本轮无 PCM")
        else:
            print(f"[server] {data}")


async def run(args: argparse.Namespace):
    stop_event = asyncio.Event()
    audio_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    tts_queue: asyncio.Queue = asyncio.Queue(maxsize=20)
    state = {
        "last_audio_ts": time.monotonic(),
        "audio_frames": 0,
        "last_report_frames": 0,
        "last_report_ts": time.monotonic(),
    }

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    blocksize = int(args.sample_rate * args.frame_ms / 1000)

    def audio_callback(indata, frames, time_info, status):
        if status:
            print(f"[mic] 状态: {status}")
        if stop_event.is_set():
            return
        chunk = bytes(indata)
        state["last_audio_ts"] = time.monotonic()
        state["audio_frames"] += 1

        def _enqueue():
            try:
                audio_queue.put_nowait(chunk)
            except asyncio.QueueFull:
                # 网络慢时丢旧数据，保持实时性
                pass

        # sounddevice 回调线程与 asyncio 事件循环不同，需线程安全投递
        loop.call_soon_threadsafe(_enqueue)

    async def monitor_loop():
        while not stop_event.is_set():
            await asyncio.sleep(2)
            now = time.monotonic()
            no_audio_for = now - state["last_audio_ts"]
            if no_audio_for > 3:
                print(f"[monitor] {no_audio_for:.1f}s 未收到麦克风音频帧，请检查输入设备/权限。")
            if now - state["last_report_ts"] >= 10:
                new_frames = state["audio_frames"] - state["last_report_frames"]
                print(
                    f"[monitor] 10s 音频帧数={new_frames}, 待发送队列={audio_queue.qsize()}, 待播放队列={tts_queue.qsize()}"
                )
                state["last_report_frames"] = state["audio_frames"]
                state["last_report_ts"] = now

    try:
        devs = sd.query_devices()
        hostapis = sd.query_hostapis()
        in_dev, out_dev = sd.default.device
        in_name = devs[in_dev]["name"] if in_dev is not None else "None"
        out_name = devs[out_dev]["name"] if out_dev is not None else "None"
        in_hapi = hostapis[devs[in_dev]["hostapi"]]["name"] if in_dev is not None else ""
        out_hapi = hostapis[devs[out_dev]["hostapi"]]["name"] if out_dev is not None else ""
        print(
            f"[audio] 默认输入: {in_dev} {in_hapi!s} {in_name!r}\n"
            f"[audio] 默认输出: {out_dev} {out_hapi!s} {out_name!r}\n"
            f"[audio] 本次将使用: in_device={args.device!s}, play_device={args.play_device!s}, play_gain={args.play_gain}"
        )
    except Exception as e:
        print(f"[audio] 设备信息读取失败: {e}")

    print("正在连接服务器...")
    async with websockets.connect(args.ws_url, max_size=None) as ws:
        ready = await ws.recv()
        print(f"[server] {ready}")

        print("开始收音。按 Ctrl+C 结束。")
        with sd.RawInputStream(
            samplerate=args.sample_rate,
            channels=args.channels,
            dtype="int16",
            blocksize=blocksize,
            callback=audio_callback,
            device=args.device,
        ):
            sender_task = asyncio.create_task(sender_loop(ws, audio_queue, stop_event))
            receiver_task = asyncio.create_task(receiver_loop(ws, tts_queue, stop_event))
            player_task = asyncio.create_task(
                tts_player_loop(
                    tts_queue, stop_event, play_device=args.play_device, play_gain=args.play_gain
                )
            )
            ping_task = asyncio.create_task(ping_loop(ws, stop_event))
            monitor_task = asyncio.create_task(monitor_loop())

            await stop_event.wait()

            try:
                await ws.send(json.dumps({"type": "flush"}))
            except Exception:
                pass

            for task in (sender_task, receiver_task, player_task, ping_task, monitor_task):
                task.cancel()
            await asyncio.gather(
                sender_task,
                receiver_task,
                player_task,
                ping_task,
                monitor_task,
                return_exceptions=True,
            )

    print("已退出。")


if __name__ == "__main__":
    _args = parse_args()
    if _args.list_devices:
        print(sd.query_devices())
        raise SystemExit(0)
    asyncio.run(run(_args))
