#!/usr/bin/env python3
"""Monitor device serial + server log for A/B camera upload comparison."""
import re
import sys
import time
from collections import Counter

import serial

SERIAL_PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyACM1"
DURATION_S = int(sys.argv[2]) if len(sys.argv) > 2 else 120
SERVER_LOG = sys.argv[3] if len(sys.argv) > 3 else ""
LABEL = sys.argv[4] if len(sys.argv) > 4 else "test"

DEVICE_PATTERNS = {
    "connect_ok": re.compile(r"ready received"),
    "connect_timeout": re.compile(r"connect timeout"),
    "skip_voice": re.compile(r"skip voice"),
    "ws_maintain": re.compile(r"WS maintain reconnect"),
    "cam_enqueue": re.compile(r"\[CAM\] camera_frame seq="),
    "cam_send_slow": re.compile(r"camera_frame send slow"),
    "cam_send_fail": re.compile(r"camera_frame send failed"),
    "send_failed": re.compile(r"send failed"),
    "stream_corrupt": re.compile(r"stream corrupt"),
    "round_ok": re.compile(r"round=\d+ continuous opus"),
    "ws_ok_record": re.compile(r"ws_ok=1"),
    "ping": re.compile(r'"type":"ping"'),
    "errno": re.compile(r"errno:"),
    "superseded": re.compile(r"superseded"),
}

SERVER_PATTERNS = {
    "connection_open": re.compile(r"connection open"),
    "asr_chat_join": re.compile(r"\[/asr_chat\] 接入"),
    "camera_ok": re.compile(r"camera_frame ok"),
    "camera_infer": re.compile(r"camera_frame device_id=.*infer_ms="),
    "superseded": re.compile(r"superseded by new connection"),
    "ping_timeout": re.compile(r"keepalive ping timeout"),
    "ws_close": re.compile(r"WebSocket 已关闭"),
}


def read_server_tail(path: str, since_line: int) -> tuple[list[str], int]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return [], since_line
    new_lines = lines[since_line:]
    return new_lines, len(lines)


def main() -> None:
    dev_counts: Counter = Counter()
    srv_counts: Counter = Counter()
    samples: list[str] = []

    server_line = 0
    if SERVER_LOG:
        try:
            with open(SERVER_LOG, "r", encoding="utf-8", errors="replace") as f:
                server_line = sum(1 for _ in f)
        except OSError:
            pass

    ser = serial.Serial(SERIAL_PORT, 115200, timeout=0.5)
    start = time.time()
    print(f"=== {LABEL} monitor {DURATION_S}s port={SERIAL_PORT} ===", flush=True)

    try:
        while time.time() - start < DURATION_S:
            raw = ser.readline()
            if raw:
                line = raw.decode("utf-8", errors="replace").rstrip()
                for name, pat in DEVICE_PATTERNS.items():
                    if pat.search(line):
                        dev_counts[name] += 1
                        if name in ("connect_timeout", "cam_send_slow", "cam_send_fail", "errno"):
                            samples.append(line[:200])
                if re.search(r"\[ASR_CHAT\]|\[CAM\]|\[WS", line):
                    if len(samples) < 30:
                        pass

            if SERVER_LOG and int(time.time() - start) % 5 == 0:
                new_lines, server_line = read_server_tail(SERVER_LOG, server_line)
                for line in new_lines:
                    for name, pat in SERVER_PATTERNS.items():
                        if pat.search(line):
                            srv_counts[name] += 1
                            if name in ("superseded", "ping_timeout", "camera_ok"):
                                samples.append("[SRV] " + line.strip()[:200])
    finally:
        ser.close()

    # final server drain
    if SERVER_LOG:
        new_lines, _ = read_server_tail(SERVER_LOG, server_line)
        for line in new_lines:
            for name, pat in SERVER_PATTERNS.items():
                if pat.search(line):
                    srv_counts[name] += 1

    print(f"\n=== {LABEL} DEVICE ({DURATION_S}s) ===", flush=True)
    for k in sorted(DEVICE_PATTERNS.keys()):
        print(f"  {k}: {dev_counts[k]}", flush=True)

    print(f"\n=== {LABEL} SERVER ===", flush=True)
    for k in sorted(SERVER_PATTERNS.keys()):
        print(f"  {k}: {srv_counts[k]}", flush=True)

    if samples:
        print(f"\n=== {LABEL} KEY SAMPLES ===", flush=True)
        for s in samples[:15]:
            print(f"  {s}", flush=True)


if __name__ == "__main__":
    main()
