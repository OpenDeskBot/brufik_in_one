"""ROM 列表、烧录子进程与串口监视。"""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("deskbot-server")

REPO_ROOT = Path(__file__).resolve().parents[5]
HARDWARE_DIR = Path(os.environ.get("OPEN_DESK_ROM_FW_DIR", REPO_ROOT / "hardware")).resolve()
FLASH_SCRIPT = HARDWARE_DIR / "flash_rom.sh"
PIO_ENV = os.environ.get("OPEN_DESK_ROM_PIO_ENV", "seeed_xiao_esp32s3")
BUILD_DIR = HARDWARE_DIR / ".pio" / "build" / PIO_ENV
RELEASES_DIR = HARDWARE_DIR / "releases"
APP_FLASH_OFFSET = 0x10000

PORT_RE = re.compile(r"^/dev/(ttyACM\d+|ttyUSB\d+|tty\.usbmodem[\w.-]+|cu\.usbmodem[\w.-]+|cu\.usbserial[\w.-]+)$")
ROM_ID_RE = re.compile(r"^[a-zA-Z0-9._:-]+$")


@dataclass
class RomEntry:
    id: str
    label: str
    kind: str
    path: str | None = None
    size: int | None = None
    mtime: float | None = None
    flash_mode: str = "pio_upload"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "path": self.path,
            "size": self.size,
            "mtime": self.mtime,
            "flash_mode": self.flash_mode,
            "downloadable": bool(self.path and Path(self.path).is_file()),
        }


@dataclass
class FlashJob:
    job_id: str
    action: str
    port: str | None
    rom_id: str | None
    status: str = "running"
    exit_code: int | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "action": self.action,
            "port": self.port,
            "rom_id": self.rom_id,
            "status": self.status,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


def validate_port(port: str) -> str:
    p = (port or "").strip()
    if not PORT_RE.match(p):
        raise ValueError("无效的串口路径")
    if not Path(p).exists():
        raise ValueError(f"串口不存在: {p}")
    return p


def validate_rom_id(rom_id: str) -> str:
    rid = (rom_id or "").strip()
    if not rid or not ROM_ID_RE.match(rid):
        raise ValueError("无效的 ROM 标识")
    return rid


def list_serial_ports() -> list[dict[str, Any]]:
    patterns = (
        "/dev/ttyACM*",
        "/dev/ttyUSB*",
        "/dev/tty.usbmodem*",
        "/dev/cu.usbmodem*",
        "/dev/cu.usbserial*",
    )
    seen: set[str] = set()
    ports: list[str] = []
    for pattern in patterns:
        for p in sorted(glob.glob(pattern)):
            if p in seen:
                continue
            seen.add(p)
            ports.append(p)
    out = []
    for p in ports:
        try:
            st = os.stat(p)
            out.append({"port": p, "mtime": st.st_mtime})
        except OSError:
            out.append({"port": p, "mtime": None})
    return out


def _stat_file(path: Path) -> tuple[int | None, float | None]:
    try:
        st = path.stat()
        return st.st_size, st.st_mtime
    except OSError:
        return None, None


def list_roms() -> list[RomEntry]:
    roms: list[RomEntry] = [
        RomEntry(
            id="source",
            label="从源码编译并烧录（推荐）",
            kind="source",
            flash_mode="pio_upload",
        )
    ]

    build_files = (
        ("firmware.bin", "最新编译 · 应用固件"),
        ("bootloader.bin", "最新编译 · Bootloader"),
        ("partitions.bin", "最新编译 · 分区表"),
    )
    for fname, label in build_files:
        path = BUILD_DIR / fname
        if path.is_file():
            size, mtime = _stat_file(path)
            roms.append(
                RomEntry(
                    id=f"build:{fname}",
                    label=label,
                    kind="build",
                    path=str(path),
                    size=size,
                    mtime=mtime,
                    flash_mode="pio_nobuild_upload" if fname == "firmware.bin" else "download_only",
                )
            )

    RELEASES_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(RELEASES_DIR.glob("*.bin")):
        size, mtime = _stat_file(path)
        roms.append(
            RomEntry(
                id=f"release:{path.name}",
                label=f"发布包 · {path.name}",
                kind="release",
                path=str(path),
                size=size,
                mtime=mtime,
                flash_mode="esptool_app",
            )
        )
    return roms


def get_rom(rom_id: str) -> RomEntry:
    rid = validate_rom_id(rom_id)
    for rom in list_roms():
        if rom.id == rid:
            return rom
    raise FileNotFoundError(f"未找到 ROM: {rid}")


def resolve_rom_path(rom_id: str) -> Path:
    rom = get_rom(rom_id)
    if not rom.path:
        raise FileNotFoundError("该 ROM 不可下载")
    path = Path(rom.path).resolve()
    allowed_roots = {BUILD_DIR.resolve(), RELEASES_DIR.resolve()}
    if not any(path == root or root in path.parents for root in allowed_roots):
        raise PermissionError("ROM 路径不在允许目录内")
    if not path.is_file():
        raise FileNotFoundError("ROM 文件不存在")
    return path


class _SerialBridge:
    """单路串口读写，供 WebSocket 订阅。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._port: str | None = None
        self._ser = None
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._listeners: list[Callable[[bytes], None]] = []

    @property
    def port(self) -> str | None:
        return self._port

    def add_listener(self, cb: Callable[[bytes], None]) -> None:
        with self._lock:
            self._listeners.append(cb)

    def remove_listener(self, cb: Callable[[bytes], None]) -> None:
        with self._lock:
            try:
                self._listeners.remove(cb)
            except ValueError:
                pass

    def _emit(self, data: bytes) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(data)
            except Exception:
                logger.exception("serial listener failed")

    def start(self, port: str, baud: int = 115200) -> None:
        import serial

        port = validate_port(port)
        self.stop()
        ser = serial.Serial(port, baudrate=baud, timeout=0.2)
        self._ser = ser
        self._port = port
        self._stop.clear()

        def _read_loop() -> None:
            while not self._stop.is_set():
                try:
                    if self._ser and self._ser.in_waiting:
                        chunk = self._ser.read(self._ser.in_waiting)
                        if chunk:
                            self._emit(chunk)
                    else:
                        time.sleep(0.05)
                except Exception as exc:
                    self._emit(f"\n[serial error] {exc}\n".encode("utf-8", errors="replace"))
                    break

        self._reader = threading.Thread(target=_read_loop, name="flash-serial-reader", daemon=True)
        self._reader.start()

    def write(self, text: str) -> None:
        if not self._ser:
            raise RuntimeError("串口监视未启动")
        payload = text if text.endswith("\n") else text + "\n"
        self._ser.write(payload.encode("utf-8", errors="replace"))
        self._ser.flush()

    def stop(self) -> None:
        self._stop.set()
        if self._reader and self._reader.is_alive():
            self._reader.join(timeout=1.5)
        self._reader = None
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
        self._ser = None
        self._port = None


class FlashManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._job: FlashJob | None = None
        self._logs: deque[str] = deque(maxlen=4000)
        self._log_seq = 0
        self.serial = _SerialBridge()

    def _append_log(self, line: str) -> None:
        with self._lock:
            self._logs.append(line.rstrip("\n"))
            self._log_seq += 1

    def log_snapshot(self, since: int = 0) -> dict[str, Any]:
        with self._lock:
            lines = list(self._logs)
            seq = self._log_seq
        if since < 0:
            since = 0
        if since >= len(lines):
            return {"seq": seq, "lines": [], "since": since}
        return {"seq": seq, "lines": lines[since:], "since": since}

    def status(self) -> dict[str, Any]:
        with self._lock:
            running = self._process is not None and self._process.poll() is None
            job = self._job.to_dict() if self._job else None
            serial_port = self.serial.port
        return {
            "running": running,
            "job": job,
            "serial_port": serial_port,
            "hardware_dir": str(HARDWARE_DIR),
            "flash_script": str(FLASH_SCRIPT),
            "pio_env": PIO_ENV,
        }

    def _set_job_finished(self, exit_code: int, error: str | None = None) -> None:
        with self._lock:
            if self._job:
                self._job.status = "failed" if exit_code != 0 else "done"
                self._job.exit_code = exit_code
                self._job.finished_at = time.time()
                self._job.error = error

    def cancel(self) -> bool:
        with self._lock:
            proc = self._process
        if proc is None or proc.poll() is not None:
            return False
        self._append_log("==> 用户取消任务")
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        self._set_job_finished(proc.returncode or -1, error="cancelled")
        with self._lock:
            self._process = None
        return True

    def _resolve_pio(self) -> str:
        candidates = [
            REPO_ROOT / ".venv" / "bin" / "pio",
            HARDWARE_DIR / ".venv" / "bin" / "pio",
            Path.home() / ".local" / "bin" / "pio",
        ]
        for c in candidates:
            if c.is_file() and os.access(c, os.X_OK):
                return str(c)
        found = shutil.which("pio")
        if found:
            return found
        raise RuntimeError("未找到 pio，请安装 PlatformIO")

    def free_serial_port(self, port: str) -> None:
        if not Path(port).exists():
            return
        for cmd, args in (
            ("lsof", ["-t", port]),
            ("fuser", ["-k", "-TERM", port]),
        ):
            if not shutil.which(cmd.split()[0]):
                continue
            try:
                out = subprocess.check_output([cmd, *args], stderr=subprocess.DEVNULL, text=True)
                for pid in out.split():
                    if pid.strip().isdigit():
                        os.kill(int(pid), 15)
            except Exception:
                pass
        time.sleep(0.2)

    def _build_command(self, action: str, port: str | None, rom_id: str | None) -> list[str]:
        if action == "build":
            if FLASH_SCRIPT.is_file():
                return ["/bin/bash", str(FLASH_SCRIPT), "build"]
            pio = self._resolve_pio()
            return [pio, "run", "-e", PIO_ENV]

        port = validate_port(port or "")
        rom = get_rom(rom_id or "source")

        if rom.id == "source" or rom.flash_mode == "pio_upload":
            if FLASH_SCRIPT.is_file():
                return ["/bin/bash", str(FLASH_SCRIPT), "upload", port]
            pio = self._resolve_pio()
            return [pio, "run", "-e", PIO_ENV, "-t", "upload", "--upload-port", port]

        if rom.flash_mode == "pio_nobuild_upload":
            pio = self._resolve_pio()
            return [pio, "run", "-e", PIO_ENV, "-t", "nobuild", "-t", "upload", "--upload-port", port]

        if rom.flash_mode == "esptool_app":
            bin_path = resolve_rom_path(rom.id)
            esptool = shutil.which("esptool.py") or shutil.which("esptool")
            if esptool:
                return [
                    esptool,
                    "--chip",
                    "esp32s3",
                    "--port",
                    port,
                    "--baud",
                    "921600",
                    "write_flash",
                    hex(APP_FLASH_OFFSET),
                    str(bin_path),
                ]
            pio = self._resolve_pio()
            return [
                pio,
                "pkg",
                "exec",
                "-e",
                PIO_ENV,
                "--",
                "esptool.py",
                "--chip",
                "esp32s3",
                "--port",
                port,
                "--baud",
                "921600",
                "write_flash",
                hex(APP_FLASH_OFFSET),
                str(bin_path),
            ]

        raise ValueError(f"ROM 不支持烧录: {rom.id}")

    def _start_process(self, action: str, port: str | None, rom_id: str | None) -> FlashJob:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                raise RuntimeError("已有烧录任务进行中")
        cmd = self._build_command(action, port, rom_id)
        job_id = f"{action}-{int(time.time() * 1000)}"
        job = FlashJob(job_id=job_id, action=action, port=port, rom_id=rom_id)
        self._append_log(f"==> 启动任务 {job_id}: {' '.join(cmd)}")
        if port:
            self.free_serial_port(port)
            self.serial.stop()

        proc = subprocess.Popen(
            cmd,
            cwd=str(HARDWARE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        def _pump() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                self._append_log(line)
            code = proc.wait()
            self._set_job_finished(code, error=None if code == 0 else f"exit {code}")
            with self._lock:
                self._process = None
            self._append_log(f"==> 任务结束 exit={code}")

        with self._lock:
            self._job = job
            self._process = proc
        threading.Thread(target=_pump, name=f"flash-{job_id}", daemon=True).start()
        return job

    def start_build(self) -> FlashJob:
        return self._start_process("build", None, None)

    def start_upload(self, port: str, rom_id: str) -> FlashJob:
        return self._start_process("upload", validate_port(port), validate_rom_id(rom_id))

    async def run_blocking(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(fn, *args, **kwargs)


flash_manager = FlashManager()
