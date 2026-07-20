#!/usr/bin/env python3
"""设备-服务端网络连通性与并发压测工具。

覆盖：
  1. HTTP /health、/api/devices 基础连通
  2. PB 下行 → pb_ack 往返延迟（经 /api/device_servo）
  3. 快速连发 PB（舵机-only）延迟分布
  4. 并发模拟：音频上行 + 相机上行 + 服务端 PB 下发

用法::

    python tools/network_connectivity_test.py \\
        --device-id deskbot_e8f60a8cf9b0 \\
        --base-url http://127.0.0.1:9000

依赖：websockets、Pillow（生成测试 JPEG）；项目 venv 已包含。
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import statistics
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import websockets
except ImportError:
    print("需要 websockets: pip install websockets", file=sys.stderr)
    raise

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore


def _http_json(
    url: str,
    timeout: float = 10.0,
    *,
    api_key: str | None = None,
) -> tuple[int, dict[str, Any]]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            data = {"raw": body}
        return exc.code, data


def _find_device(devices_payload: dict, device_id: str) -> dict | None:
    for d in devices_payload.get("devices") or []:
        if str(d.get("device_id") or "") == device_id:
            return d
    return None


def _device_pb_ack_mono(dev: dict) -> float | None:
    v = dev.get("last_pb_ack_mono")
    return float(v) if v is not None else None


def _make_test_jpeg(width: int = 320, height: int = 240) -> bytes:
    if Image is None:
        # 最小合法 JPEG SOI/EOI（服务端会拒，但可测 WS 吞吐）
        return b"\xff\xd8\xff\xd9"
    img = Image.new("RGB", (width, height), color=(40, 120, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=18)
    return buf.getvalue()


def _opus_silence_frame() -> bytes:
    """极短 Opus 静音帧（长度前缀格式与固件 batch 一致）。"""
    # 2 字节大端长度 + 1 字节 dummy opus payload
    payload = b"\x00"
    return struct.pack(">H", len(payload)) + payload


@dataclass
class TestReport:
    device_id: str
    base_url: str
    checks: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    pb_ack_latencies_ms: list[float] = field(default_factory=list)
    pb_burst_latencies_ms: list[float] = field(default_factory=list)
    concurrent: dict[str, Any] = field(default_factory=dict)

    def ok(self, msg: str) -> None:
        self.checks.append(f"PASS: {msg}")

    def fail(self, msg: str) -> None:
        self.failures.append(f"FAIL: {msg}")

    def summary(self) -> str:
        lines = [
            "",
            "=" * 60,
            f"网络测试报告 device_id={self.device_id}",
            f"服务端: {self.base_url}",
            "=" * 60,
        ]
        lines.extend(self.checks)
        if self.pb_ack_latencies_ms:
            lines.append(
                f"PB 单次 ack 延迟 ms: min={min(self.pb_ack_latencies_ms):.0f} "
                f"p50={statistics.median(self.pb_ack_latencies_ms):.0f} "
                f"max={max(self.pb_ack_latencies_ms):.0f} "
                f"n={len(self.pb_ack_latencies_ms)}"
            )
        if self.pb_burst_latencies_ms:
            lines.append(
                f"PB 连发 ack 延迟 ms: min={min(self.pb_burst_latencies_ms):.0f} "
                f"p50={statistics.median(self.pb_burst_latencies_ms):.0f} "
                f"p95={sorted(self.pb_burst_latencies_ms)[int(len(self.pb_burst_latencies_ms)*0.95)-1]:.0f} "
                f"max={max(self.pb_burst_latencies_ms):.0f} "
                f"n={len(self.pb_burst_latencies_ms)}"
            )
        if self.concurrent:
            lines.append(f"并发压测: {json.dumps(self.concurrent, ensure_ascii=False)}")
        if self.failures:
            lines.append("--- 失败项 ---")
            lines.extend(self.failures)
            lines.append(f"结论: {len(self.failures)} 项失败")
        else:
            lines.append("结论: 全部通过")
        lines.append("=" * 60)
        return "\n".join(lines)


async def _mock_uplink_audio(
    ws_url: str,
    duration_sec: float,
    frame_interval: float,
    stats: dict[str, Any],
) -> None:
    """模拟设备 Opus 音频上行（JSON + binary batch）。"""
    sent = 0
    failed = 0
    t_end = time.monotonic() + duration_sec
    async with websockets.connect(ws_url, max_size=None, open_timeout=15) as ws:
        ready = await asyncio.wait_for(ws.recv(), timeout=10)
        if isinstance(ready, str):
            stats["ready"] = json.loads(ready)
        while time.monotonic() < t_end:
            batch = _opus_silence_frame() * 5
            hdr = json.dumps(
                {
                    "type": "audio",
                    "codec": "opus",
                    "next_bin_len": len(batch),
                    "sr": 16000,
                    "ch": 1,
                    "frames": 5,
                }
            )
            try:
                await ws.send(hdr)
                await ws.send(batch)
                sent += 1
            except Exception as exc:
                failed += 1
                stats["last_audio_error"] = str(exc)
            await asyncio.sleep(frame_interval)
        try:
            await ws.send(json.dumps({"type": "flush"}))
        except Exception:
            pass
    stats["audio_batches_sent"] = sent
    stats["audio_batches_failed"] = failed


async def _mock_uplink_camera(
    ws_url: str,
    duration_sec: float,
    fps: float,
    jpeg: bytes,
    stats: dict[str, Any],
) -> None:
    """经 /camera_uplink 模拟 JPEG 分块上行（与固件一致：裸 binary）。"""
    sent = 0
    failed = 0
    interval = 1.0 / max(0.5, fps)
    t_end = time.monotonic() + duration_sec
    chunk = 512
    async with websockets.connect(ws_url, max_size=None, open_timeout=15) as ws:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=2)
                if isinstance(msg, str) and "ready" in msg:
                    stats["cam_ready"] = True
                    break
            except asyncio.TimeoutError:
                break
        while time.monotonic() < t_end:
            try:
                off = 0
                while off < len(jpeg):
                    n = min(chunk, len(jpeg) - off)
                    await ws.send(jpeg[off : off + n])
                    off += n
                sent += 1
            except Exception as exc:
                failed += 1
                stats["last_camera_error"] = str(exc)
            await asyncio.sleep(interval)
    stats["camera_frames_sent"] = sent
    stats["camera_frames_failed"] = failed


async def _send_pb_servo(
    base_url: str,
    device_id: str,
    dyaw: float,
    dpitch: float,
    ms: int = 200,
    *,
    api_key: str | None = None,
) -> tuple[float, dict]:
    """HTTP 下发 pb_single(servo)，返回 (发送时刻 mono, 响应 JSON)。"""
    q = urllib.parse.urlencode(
        {
            "device_id": device_id,
            "dyaw": dyaw,
            "dpitch": dpitch,
            "ms": ms,
            "xm": 1,
            "ym": 1,
        }
    )
    t0 = time.monotonic()
    status, data = await asyncio.to_thread(
        _http_json, f"{base_url.rstrip('/')}/api/device_servo?{q}", 15.0, api_key=api_key
    )
    if status != 200:
        raise RuntimeError(f"device_servo HTTP {status}: {data}")
    return t0, data


async def _wait_pb_ack(
    base_url: str,
    device_id: str,
    since_mono: float,
    timeout_sec: float = 8.0,
    *,
    api_key: str | None = None,
) -> float | None:
    """轮询 /api/devices 直到 last_pb_ack_mono > since_mono。"""
    deadline = time.monotonic() + timeout_sec
    url = f"{base_url.rstrip('/')}/api/devices"
    while time.monotonic() < deadline:
        _, data = await asyncio.to_thread(_http_json, url, 8.0, api_key=api_key)
        dev = _find_device(data, device_id)
        if dev:
            mono = _device_pb_ack_mono(dev)
            if mono is not None and mono > since_mono + 0.001:
                return (mono - since_mono) * 1000.0
        await asyncio.sleep(0.05)
    return None


async def _monitor_real_camera_log(
    log_path: str,
    device_id: str,
    duration_sec: float,
    stats: dict[str, Any],
) -> None:
    """从服务端日志统计真机 camera_uplink 帧数（device_id 精确匹配，排除 _nettest）。"""
    needle = f"camera_frame ok device_id={device_id} "
    t_end = time.monotonic() + duration_sec
    pos = 0
    p = Path(log_path)
    if p.is_file():
        pos = p.stat().st_size
    else:
        stats["log_missing"] = log_path
        await asyncio.sleep(duration_sec)
        return
    count = 0
    last_bytes = 0
    while time.monotonic() < t_end:
        if not p.is_file():
            await asyncio.sleep(0.5)
            continue
        try:
            with p.open("rb") as f:
                f.seek(pos)
                chunk = f.read()
                pos = f.tell()
            if chunk:
                text = chunk.decode("utf-8", errors="replace")
                for line in text.splitlines():
                    if needle in line and "_nettest" not in line:
                        count += 1
                        # bytes=NNNN
                        idx = line.find("bytes=")
                        if idx >= 0:
                            try:
                                last_bytes = int(line[idx + 6 :].split()[0])
                            except ValueError:
                                pass
        except OSError as exc:
            stats["log_read_error"] = str(exc)
        await asyncio.sleep(0.25)
    stats["real_camera_frames"] = count
    stats["last_jpeg_bytes"] = last_bytes
    stats["log_path"] = log_path


async def run_tests(args: argparse.Namespace) -> TestReport:
    base = args.base_url.rstrip("/")
    device_id = args.device_id
    api_key = args.api_key
    if not api_key:
        key_file = Path(__file__).resolve().parent.parent / "data" / ".free_api_key"
        if key_file.is_file():
            for line in key_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("api_key="):
                    api_key = line.split("=", 1)[1].strip()
                    break
    report = TestReport(device_id=device_id, base_url=base)

    # --- 1. 基础连通 ---
    status, health = await asyncio.to_thread(_http_json, f"{base}/health", 8.0, api_key=api_key)
    if status == 200:
        report.ok(f"/health -> {status}")
    else:
        report.fail(f"/health -> {status} {health}")

    _, devs = await asyncio.to_thread(_http_json, f"{base}/api/devices", 8.0, api_key=api_key)
    dev = _find_device(devs, device_id)
    if not dev:
        report.fail(f"设备 {device_id} 未在 /api/devices 中在线")
        print(report.summary())
        return report
    ch = dev.get("channels") or {}
    report.ok(
        f"设备在线 channels={ch} last_seen={dev.get('last_seen')} "
        f"asr_chat={ch.get('asr_chat', 0)} camera_uplink={ch.get('camera_uplink', 0)}"
    )
    if not ch.get("asr_chat"):
        report.fail("asr_chat 通道未连接")
    if args.require_camera and not ch.get("camera_uplink"):
        report.fail("camera_uplink 通道未连接（加 --no-require-camera 可跳过）")

    # --- 2. 单次 PB 往返 ---
    for i in range(args.pb_single_rounds):
        t0, resp = await _send_pb_servo(
            base, device_id, dyaw=2.0, dpitch=-1.0, ms=150, api_key=api_key
        )
        delivered = int(resp.get("delivered") or 0)
        if delivered <= 0:
            report.fail(f"PB 单次 delivered=0 round={i+1} resp={resp}")
            continue
        lat = await _wait_pb_ack(
            base, device_id, t0, timeout_sec=args.ack_timeout, api_key=api_key
        )
        if lat is None:
            report.fail(f"PB 单次 round={i+1} 未在 {args.ack_timeout}s 内收到 pb_ack")
        else:
            report.pb_ack_latencies_ms.append(lat)
            report.ok(f"PB 单次 round={i+1} ack 延迟 {lat:.0f}ms delivered={delivered}")

    # --- 3. PB 连发 ---
    burst_n = args.pb_burst_count
    interval = args.pb_burst_interval_ms / 1000.0
    for i in range(burst_n):
        t0, resp = await _send_pb_servo(
            base,
            device_id,
            dyaw=float((i % 5) - 2),
            dpitch=float((i % 3) - 1),
            ms=100,
            api_key=api_key,
        )
        if int(resp.get("delivered") or 0) <= 0:
            report.fail(f"PB 连发 idx={i} delivered=0")
        lat = await _wait_pb_ack(
            base, device_id, t0, timeout_sec=args.ack_timeout, api_key=api_key
        )
        if lat is not None:
            report.pb_burst_latencies_ms.append(lat)
        else:
            report.fail(f"PB 连发 idx={i} ack 超时")
        if interval > 0 and i + 1 < burst_n:
            await asyncio.sleep(interval)

    if report.pb_burst_latencies_ms:
        p50 = statistics.median(report.pb_burst_latencies_ms)
        if p50 > args.pb_burst_p50_limit_ms:
            report.fail(
                f"PB 连发 p50={p50:.0f}ms 超过阈值 {args.pb_burst_p50_limit_ms}ms"
            )
        else:
            report.ok(f"PB 连发 p50={p50:.0f}ms (阈值 {args.pb_burst_p50_limit_ms}ms)")

    # --- 4. 并发压测（mock 客户端 + 真实设备 PB）---
    if args.concurrent_sec > 0:
        mock_id = f"{device_id}_nettest"
        ws_base = base.replace("http://", "ws://").replace("https://", "wss://")
        asr_url = f"{ws_base}/asr_chat?device_id={mock_id}"
        if api_key:
            asr_url += f"&api_key={urllib.parse.quote(api_key)}"
        cam_url = f"{ws_base}/camera_uplink?device_id={mock_id}"
        if api_key:
            cam_url += f"&api_key={urllib.parse.quote(api_key)}"

        jpeg = _make_test_jpeg()
        audio_stats: dict[str, Any] = {}
        cam_stats: dict[str, Any] = {}
        pb_ok = 0
        pb_fail = 0

        async def pb_while_load() -> None:
            nonlocal pb_ok, pb_fail
            t_end = time.monotonic() + args.concurrent_sec
            while time.monotonic() < t_end:
                try:
                    t0, resp = await _send_pb_servo(
                        base, device_id, dyaw=1.0, dpitch=0.0, ms=80, api_key=api_key
                    )
                    if int(resp.get("delivered") or 0) > 0:
                        lat = await _wait_pb_ack(
                            base, device_id, t0, timeout_sec=5.0, api_key=api_key
                        )
                        if lat is not None:
                            pb_ok += 1
                        else:
                            pb_fail += 1
                    else:
                        pb_fail += 1
                except Exception:
                    pb_fail += 1
                await asyncio.sleep(0.3)

        tasks = [
            pb_while_load(),
        ]
        if args.mock_audio:
            tasks.append(
                _mock_uplink_audio(
                    asr_url,
                    args.concurrent_sec,
                    args.audio_interval_ms / 1000.0,
                    audio_stats,
                )
            )
        if args.mock_camera:
            tasks.append(
                _mock_uplink_camera(
                    cam_url,
                    args.concurrent_sec,
                    args.camera_fps,
                    jpeg,
                    cam_stats,
                )
            )
        real_cam_stats: dict[str, Any] = {}
        if args.server_log:
            tasks.append(
                _monitor_real_camera_log(
                    args.server_log,
                    device_id,
                    args.concurrent_sec + 2.0,
                    real_cam_stats,
                )
            )

        await asyncio.gather(*tasks)

        _, devs_after = await asyncio.to_thread(
            _http_json, f"{base}/api/devices", 8.0, api_key=api_key
        )
        real_dev = _find_device(devs_after, device_id)
        still_online = bool(real_dev and (real_dev.get("channels") or {}).get("asr_chat"))

        report.concurrent = {
            "duration_sec": args.concurrent_sec,
            "mock_audio": audio_stats if args.mock_audio else None,
            "mock_camera": cam_stats if args.mock_camera else None,
            "real_device_camera": real_cam_stats or None,
            "real_device_pb_ok": pb_ok,
            "real_device_pb_fail": pb_fail,
            "real_device_still_online": still_online,
        }
        if real_cam_stats.get("real_camera_frames", 0) == 0 and args.require_real_camera:
            report.fail(
                f"并发期间未检测到真机相机上传（log={args.server_log or '未指定'}）"
            )
        elif real_cam_stats.get("real_camera_frames"):
            report.ok(
                f"真机相机并发上传 {real_cam_stats['real_camera_frames']} 帧"
                f"（末帧约 {real_cam_stats.get('last_jpeg_bytes', 0)} bytes）"
            )
        if still_online:
            report.ok(
                f"并发 {args.concurrent_sec}s 后真实设备仍在线 "
                f"(pb_ok={pb_ok} pb_fail={pb_fail})"
            )
        else:
            report.fail(f"并发压测后真实设备 asr_chat 离线")
        if pb_fail > pb_ok:
            report.fail(f"并发期间 PB 失败率过高 fail={pb_fail} ok={pb_ok}")

    print(report.summary())
    return report


def main() -> int:
    p = argparse.ArgumentParser(description="Deskbot 设备-服务端网络连通性测试")
    p.add_argument("--device-id", required=True, help="真实在线设备 ID")
    p.add_argument("--base-url", default="http://127.0.0.1:9000", help="deskbot-server HTTP 基址")
    p.add_argument("--api-key", default="", help="mock WS 用的 API Key（默认可读 data/.free_api_key）")
    p.add_argument("--pb-single-rounds", type=int, default=3, help="单次 PB 往返轮数")
    p.add_argument("--pb-burst-count", type=int, default=8, help="PB 连发次数")
    p.add_argument("--pb-burst-interval-ms", type=float, default=50, help="连发间隔 ms")
    p.add_argument("--pb-burst-p50-limit-ms", type=float, default=800, help="连发 p50 延迟上限 ms")
    p.add_argument("--ack-timeout", type=float, default=8.0, help="等待 pb_ack 超时秒")
    p.add_argument("--concurrent-sec", type=float, default=15.0, help="并发压测时长（0=跳过）")
    p.add_argument("--audio-interval-ms", type=float, default=200, help="mock 音频 batch 间隔")
    p.add_argument("--camera-fps", type=float, default=5.0, help="mock 相机帧率")
    p.add_argument("--mock-audio", action="store_true", default=True, help="启用 mock 音频上行")
    p.add_argument("--no-mock-audio", action="store_false", dest="mock_audio")
    p.add_argument("--mock-camera", action="store_true", default=True, help="启用 mock 相机上行")
    p.add_argument("--no-mock-camera", action="store_false", dest="mock_camera")
    p.add_argument(
        "--server-log",
        default="",
        help="服务端日志路径，用于统计真机 camera_uplink 帧数",
    )
    p.add_argument(
        "--require-real-camera",
        action="store_true",
        default=False,
        help="并发压测期间必须检测到真机相机上传",
    )
    p.add_argument("--require-camera", action="store_true", default=True)
    p.add_argument("--no-require-camera", action="store_false", dest="require_camera")
    args = p.parse_args()

    report = asyncio.run(run_tests(args))
    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
