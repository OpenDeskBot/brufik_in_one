"""共享常量。"""
from __future__ import annotations

import os

from deskbot_server.paths import DATA_DIR, MODELS_DIR

LOG_FILE = os.environ.get("DESKBOT_SERVER_LOG_FILE", "app.log")
SAFE_SEND_TIMEOUT = float(os.environ.get("WS_SEND_TIMEOUT_SEC", "10.0"))
# pb：JSON 解析后再收 binary；chunk 间给设备消化时间（秒，0=关闭）
PB_JSON_BIN_GAP_SEC = max(
    0.0, float(os.environ.get("PB_JSON_BIN_GAP_MS", "50")) / 1000.0
)
PB_CHUNK_GAP_SEC = max(
    0.0, float(os.environ.get("PB_CHUNK_GAP_MS", "150")) / 1000.0
)
# 有 audio 的 pb 片：发完后等待设备 pb_ack.idx>=该片 idx 再发下一片（0=关闭）
PB_WAIT_ACK = os.environ.get("PB_WAIT_ACK", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
# ESP32 WS 单帧 TEXT 上限（字节）；超限对端 close 1009 message too big
PB_MAX_WIRE_JSON_BYTES = max(
    4096, int(os.environ.get("PB_MAX_WIRE_JSON_BYTES", "14000"))
)
# ESP32 WS 单帧 BINARY（PCM）上限；默认按 10s@24kHz mono s16le（10000ms→480000B）
_PB_PCM_MS_CAP_DEFAULT = 10000
PB_MAX_PCM_BIN_BYTES = max(
    4096,
    int(
        os.environ.get(
            "PB_MAX_PCM_BIN_BYTES",
            str((_PB_PCM_MS_CAP_DEFAULT * 24000 // 1000) * 2),
        )
    ),
)


def pb_max_chunk_ms_for_pcm(
    sample_rate: int = 24000, *, max_pcm_bytes: int | None = None
) -> int:
    """由 R6 反推 ``chunk_ms`` 上限，使 mono s16le PCM 字节数不超过 ``max_pcm_bytes``。"""
    limit = PB_MAX_PCM_BIN_BYTES if max_pcm_bytes is None else max_pcm_bytes
    sr = max(1, int(sample_rate))
    return max(100, (limit // 2) * 1000 // sr)

GLOBAL_DATA_DIR = DATA_DIR / "global"
SERVO_CFG_FILE = str(DATA_DIR / "servo.json")
CAMERA_FACE_CFG_FILE = str(GLOBAL_DATA_DIR / "camera_face.json")
FACE_PROFILES_FILE = str(DATA_DIR / "face_profiles.json")
FACE_DESIGN_FILE = str(GLOBAL_DATA_DIR / "deskbot-face.json")
USER_MEMORY_FILE = str(DATA_DIR / "user_memory.json")
DEVICE_VOLUME_FILE = str(DATA_DIR / "device_volume.json")

CAMERA_VIEW_PATH = "/camera_view"
DEVICE_PIPELINE_PATH = "/device_pipeline"
DEVICE_PIPELINE_MAX_EVENTS = 100

CAMERA_MODEL_DEFAULT_PATH = str(MODELS_DIR / "mediapipe" / "face_landmarker.task")
