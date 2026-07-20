"""豆包语音 2.0 TTS（火山引擎 V3 双向 WebSocket）客户端。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

from deskbot_server.env import load_dotenv
from deskbot_server.tts.protocols import (
    EventType,
    MsgType,
    finish_connection,
    finish_session,
    new_session_id,
    receive_message,
    start_connection,
    start_session,
    task_request,
    wait_for_event,
)

logger = logging.getLogger("deskbot-server")

DEFAULT_WS_URL = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"
DEFAULT_RESOURCE_ID = "seed-tts-2.0"
DEFAULT_MODEL = "seed-tts-2.0-expressive"
DEFAULT_SPEAKER = "zh_female_vv_uranus_bigtts"
DEFAULT_VOICE_CLONE_RESOURCE_ID = "seed-icl-2.0"
DEFAULT_VOICE_CLONE_URL = "https://openspeech.bytedance.com/api/v3/tts/voice_clone"
DEFAULT_VOICE_STATUS_URL = "https://openspeech.bytedance.com/api/v3/tts/get_voice"
TTS_API_KEY_ENV_NAMES = (
    "DOUBAO_TTS_API_KEY",
    "VOLCENGINE_TTS_API_KEY",
    "SEED_TTS_API_KEY",
    "BYTEPLUS_SEED_SPEECH_API_KEY",
)


@dataclass(frozen=True)
class DoubaoTtsConfig:
    api_key: str
    speaker: str = ""
    resource_id: str = DEFAULT_RESOURCE_ID
    model: str = DEFAULT_MODEL
    ws_url: str = DEFAULT_WS_URL
    sample_rate: int = 24000
    audio_format: str = "pcm"
    enable_timestamp: bool = True
    app_id: str = ""
    access_token: str = ""
    voice_clone_resource_id: str = DEFAULT_VOICE_CLONE_RESOURCE_ID
    voice_clone_url: str = DEFAULT_VOICE_CLONE_URL
    voice_status_url: str = DEFAULT_VOICE_STATUS_URL

    def ws_headers(self) -> dict[str, str]:
        return {
            "X-Api-Key": self.api_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Connect-Id": uuid4().hex,
        }

    def masked(self) -> dict[str, Any]:
        return {
            "api_key": "",
            "api_key_set": bool(self.api_key),
            "speaker": self.speaker,
            "resource_id": self.resource_id,
            "model": self.model,
            "ws_url": self.ws_url,
            "sample_rate": self.sample_rate,
            "audio_format": self.audio_format,
            "enable_timestamp": self.enable_timestamp,
            "app_id": "",
            "app_id_set": bool(self.app_id),
            "access_token": "",
            "access_token_set": bool(self.access_token),
            "voice_clone_resource_id": self.voice_clone_resource_id,
            "voice_clone_url": self.voice_clone_url,
            "voice_status_url": self.voice_status_url,
        }


def _mask_secret(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if len(raw) <= 6:
        return "*" * len(raw)
    return raw[:3] + "*" * (len(raw) - 6) + raw[-3:]


def _is_masked_secret(value: str) -> bool:
    """判断是否为 masked() 产生的占位串，避免误当作真实密钥。"""
    raw = (value or "").strip()
    if not raw or "*" not in raw:
        return False
    if len(raw) <= 6:
        return all(c == "*" for c in raw)
    return raw[3:-3] == "*" * (len(raw) - 6)


def resolve_optional_secret(incoming, fallback: str) -> str:
    """表单留空或脱敏占位时不覆盖，使用 fallback（.env / 进程环境）。"""
    raw = str(incoming or "").strip()
    if not raw or _is_masked_secret(raw):
        return (fallback or "").strip()
    return raw


def _resolve_tts_api_key() -> str:
    for name in TTS_API_KEY_ENV_NAMES:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def load_doubao_tts_config() -> DoubaoTtsConfig:
    load_dotenv()
    speaker = (os.environ.get("DOUBAO_TTS_SPEAKER") or "").strip()
    resource_id = (os.environ.get("DOUBAO_TTS_RESOURCE_ID") or DEFAULT_RESOURCE_ID).strip()
    model = (os.environ.get("DOUBAO_TTS_MODEL") or DEFAULT_MODEL).strip()
    from deskbot_server.tts.speakers import find_doubao_tts_speaker_preset, suggest_resource_id

    expected_rid = suggest_resource_id(speaker)
    preset = find_doubao_tts_speaker_preset(speaker)
    if preset and preset.resource_id:
        expected_rid = preset.resource_id.strip()
    if expected_rid and resource_id != expected_rid:
        logger.info(
            "DOUBAO_TTS_RESOURCE_ID=%s 与 speaker=%s 不匹配，自动改用 %s",
            resource_id,
            speaker,
            expected_rid,
        )
        resource_id = expected_rid
    if resource_id == "seed-tts-1.0" and model.startswith("seed-tts-2"):
        logger.info(
            "speaker=%s 使用 seed-tts-1.0，忽略 2.0 model=%s",
            speaker,
            model,
        )
        model = ""
    return DoubaoTtsConfig(
        api_key=_resolve_tts_api_key(),
        speaker=(os.environ.get("DOUBAO_TTS_SPEAKER") or DEFAULT_SPEAKER).strip(),
        resource_id=(os.environ.get("DOUBAO_TTS_RESOURCE_ID") or DEFAULT_RESOURCE_ID).strip(),
        model=(os.environ.get("DOUBAO_TTS_MODEL") or DEFAULT_MODEL).strip(),
        ws_url=(os.environ.get("DOUBAO_TTS_WS_URL") or DEFAULT_WS_URL).strip(),
        sample_rate=int(os.environ.get("DOUBAO_TTS_SAMPLE_RATE") or 24000),
        audio_format=(os.environ.get("DOUBAO_TTS_FORMAT") or "pcm").strip(),
        enable_timestamp=(os.environ.get("DOUBAO_TTS_ENABLE_TIMESTAMP") or "1").strip().lower()
        not in ("0", "false", "no", "off"),
        app_id=(os.environ.get("DOUBAO_TTS_APP_ID") or "").strip(),
        access_token=(os.environ.get("DOUBAO_TTS_ACCESS_TOKEN") or "").strip(),
        voice_clone_resource_id=(
            os.environ.get("DOUBAO_TTS_VOICE_CLONE_RESOURCE_ID") or DEFAULT_VOICE_CLONE_RESOURCE_ID
        ).strip(),
        voice_clone_url=(os.environ.get("DOUBAO_TTS_VOICE_CLONE_URL") or DEFAULT_VOICE_CLONE_URL).strip(),
        voice_status_url=(os.environ.get("DOUBAO_TTS_VOICE_STATUS_URL") or DEFAULT_VOICE_STATUS_URL).strip(),
    )


def _build_start_session_payload(cfg: DoubaoTtsConfig) -> bytes:
    audio_params: dict[str, Any] = {
        "format": cfg.audio_format,
        "sample_rate": cfg.sample_rate,
    }
    if cfg.enable_timestamp:
        audio_params["enable_timestamp"] = True
    req_params: dict[str, Any] = {
        "speaker": cfg.speaker,
        "audio_params": audio_params,
    }
    if cfg.model:
        req_params["model"] = cfg.model
    if cfg.enable_timestamp:
        req_params["enable_timestamp"] = True
        req_params["enable_subtitle"] = True
        req_params["additions"] = json.dumps(
            {"enable_timestamp": True, "enable_subtitle": True},
            ensure_ascii=False,
        )
    body = {
        "user": {"uid": uuid4().hex},
        "event": EventType.StartSession,
        "namespace": "BidirectionalTTS",
        "req_params": req_params,
    }
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def _describe_ws_connect_error(exc: Exception) -> str:
    if isinstance(exc, InvalidStatus):
        body = (exc.response.body or b"").decode("utf-8", errors="replace").strip()
        status = exc.response.status_code
        hint = ""
        if status == 401:
            hint = (
                "（鉴权失败：请核对豆包语音/Seed Speech API Key，"
                "使用 DOUBAO_TTS_API_KEY，不要使用 ARK_API_KEY）"
            )
        elif status == 403 and "not granted" in body:
            hint = "（资源未授权：请在控制台开通对应 Resource ID，例如 seed-tts-2.0）"
        elif status == 400 and "no token" in body:
            hint = "（未提供 API Key：请配置 DOUBAO_TTS_API_KEY）"
        detail = f"HTTP {status}"
        if body:
            detail += f": {body}"
        return f"WebSocket 连接被拒 {detail}{hint}"
    return f"{type(exc).__name__}: {exc}"


def _parse_session_meta(payload: bytes) -> tuple[int, str]:
    raw = payload.decode("utf-8", errors="replace") if payload else ""
    try:
        meta = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return 0, raw or "unknown"
    return int(meta.get("status_code", 0) or 0), str(meta.get("message", raw or "unknown"))


@dataclass
class DoubaoTtsResult:
    pcm: bytes
    sample_rate: int
    elapsed_ms: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    sentence_end: dict[str, Any] | None = None
    subtitles: list[Any] = field(default_factory=list)


def _config_pool_key(cfg: DoubaoTtsConfig) -> tuple[Any, ...]:
    return (
        cfg.ws_url,
        cfg.api_key,
        cfg.speaker,
        cfg.resource_id,
        cfg.model,
        cfg.sample_rate,
        cfg.audio_format,
        cfg.enable_timestamp,
    )


class DoubaoTtsConnection:
    """复用同一 WebSocket 连接，多次 StartSession 合成，避免每句重连。"""

    def __init__(self, cfg: DoubaoTtsConfig) -> None:
        self._cfg = cfg
        self._ws: Any | None = None
        self._lock = asyncio.Lock()
        self._ready = False

    async def synthesize(self, text: str) -> DoubaoTtsResult:
        clean = (text or "").strip()
        if not clean:
            raise ValueError("空文本")
        if not self._cfg.api_key:
            raise ValueError("豆包 TTS 未配置 DOUBAO_TTS_API_KEY")
        if not self._cfg.speaker:
            raise ValueError("未设置 speaker（音色）")

        async with self._lock:
            t0 = time.monotonic()
            for attempt in range(2):
                try:
                    await self._ensure_ready()
                    pcm, events, sentence_end, subtitles = await self._synthesize_once(clean)
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    logger.info(
                        "豆包 TTS 合成完成 speaker=%r pcm_bytes=%d elapsed_ms=%d segs_hint words=%d phonemes=%d",
                        self._cfg.speaker,
                        len(pcm),
                        elapsed_ms,
                        len((sentence_end or {}).get("words") or []),
                        len((sentence_end or {}).get("phonemes") or []),
                    )
                    return DoubaoTtsResult(
                        pcm=bytes(pcm),
                        sample_rate=self._cfg.sample_rate,
                        elapsed_ms=elapsed_ms,
                        events=events,
                        sentence_end=sentence_end,
                        subtitles=subtitles,
                    )
                except (
                    ConnectionClosed,
                    InvalidStatus,
                    RuntimeError,
                    OSError,
                    websockets.WebSocketException,
                ) as exc:
                    logger.warning(
                        "豆包 TTS 会话失败 attempt=%d err=%s，将重连",
                        attempt + 1,
                        exc,
                    )
                    await self._reset()
                    if attempt == 1:
                        raise
            raise RuntimeError("豆包 TTS 合成失败")

    async def close(self) -> None:
        async with self._lock:
            await self._reset()

    async def _ensure_ready(self) -> None:
        if self._ws is not None and self._ready:
            return
        await self._reset()
        cfg = self._cfg
        ws = await websockets.connect(
            cfg.ws_url,
            additional_headers=cfg.ws_headers(),
            max_size=None,
            open_timeout=30,
            ping_interval=20,
            ping_timeout=20,
        )
        try:
            await start_connection(ws)
            await wait_for_event(ws, MsgType.FullServerResponse, EventType.ConnectionStarted)
        except Exception:
            await ws.close()
            raise
        self._ws = ws
        self._ready = True
        logger.info(
            "豆包 TTS WebSocket 长连接已建立 url=%s speaker=%r",
            cfg.ws_url,
            cfg.speaker,
        )

    async def _reset(self) -> None:
        self._ready = False
        ws = self._ws
        self._ws = None
        if ws is None:
            return
        try:
            await finish_connection(ws)
            await asyncio.wait_for(receive_message(ws), timeout=2.0)
        except Exception:
            pass
        try:
            await ws.close()
        except Exception:
            pass

    async def _synthesize_once(
        self, clean: str
    ) -> tuple[bytearray, list[dict[str, Any]], dict[str, Any] | None, list[Any]]:
        ws = self._ws
        if ws is None:
            raise RuntimeError("WebSocket 未连接")
        cfg = self._cfg
        session_id = new_session_id()
        pcm = bytearray()
        events: list[dict[str, Any]] = []
        sentence_end: dict[str, Any] | None = None
        subtitles: list[Any] = []

        await start_session(ws, _build_start_session_payload(cfg), session_id)
        session_msg = await receive_message(ws)
        events.append(_event_row(session_msg))
        if session_msg.event == EventType.SessionFailed:
            code, message = _parse_session_meta(session_msg.payload)
            raise RuntimeError(f"SessionFailed ({code}): {message}")
        if session_msg.event != EventType.SessionStarted:
            raise RuntimeError(f"期望 SessionStarted，收到 {session_msg.event}")

        task_body = json.dumps({"req_params": {"text": clean}}, ensure_ascii=False).encode("utf-8")
        await task_request(ws, task_body, session_id)
        await finish_session(ws, session_id)

        while True:
            msg = await receive_message(ws)
            events.append(_event_row(msg))
            if msg.type == MsgType.Error:
                err_text = msg.payload.decode("utf-8", errors="replace")
                raise RuntimeError(f"协议错误 ({msg.error_code}): {err_text}")
            if msg.type == MsgType.AudioOnlyServer and msg.event == EventType.TTSResponse:
                if msg.payload:
                    pcm.extend(msg.payload)
                continue
            if msg.event == EventType.TTSSubtitle and msg.payload:
                try:
                    subtitles.append(json.loads(msg.payload.decode("utf-8")))
                except json.JSONDecodeError:
                    pass
                continue
            if msg.event == EventType.TTSSentenceEnd and msg.payload:
                try:
                    sentence_end = json.loads(msg.payload.decode("utf-8"))
                except json.JSONDecodeError:
                    pass
                continue
            if msg.event == EventType.SessionFinished:
                break
            if msg.event == EventType.SessionFailed:
                code, message = _parse_session_meta(msg.payload)
                raise RuntimeError(f"SessionFailed ({code}): {message}")

        return pcm, events, sentence_end, subtitles


_pool_lock = asyncio.Lock()
_pools: dict[tuple[Any, ...], DoubaoTtsConnection] = {}


async def get_doubao_tts_connection(cfg: DoubaoTtsConfig) -> DoubaoTtsConnection:
    key = _config_pool_key(cfg)
    async with _pool_lock:
        conn = _pools.get(key)
        if conn is None:
            conn = DoubaoTtsConnection(cfg)
            _pools[key] = conn
        return conn


async def synthesize_doubao_tts(text: str, cfg: DoubaoTtsConfig) -> DoubaoTtsResult:
    """合成整段文本；复用进程内 WebSocket 长连接。"""
    conn = await get_doubao_tts_connection(cfg)
    try:
        return await conn.synthesize(text)
    except InvalidStatus as exc:
        err = _describe_ws_connect_error(exc)
        logger.error(
            "豆包 TTS WebSocket 连接失败 url=%s resource_id=%s err=%s",
            cfg.ws_url,
            cfg.resource_id,
            err,
            exc_info=exc,
        )
        raise RuntimeError(err) from exc


def _event_row(msg) -> dict[str, Any]:
    row: dict[str, Any] = {
        "type": str(msg.type),
        "event": str(msg.event),
        "session_id": msg.session_id,
        "payload_bytes": len(msg.payload or b""),
    }
    if msg.type == MsgType.Error:
        row["error_code"] = msg.error_code
        row["payload"] = (msg.payload or b"").decode("utf-8", errors="replace")
    elif msg.type != MsgType.AudioOnlyServer and msg.payload:
        try:
            row["payload"] = json.loads(msg.payload.decode("utf-8"))
        except json.JSONDecodeError:
            row["payload"] = (msg.payload or b"").decode("utf-8", errors="replace")
    return row
