"""火山引擎豆包语音声音复刻 V3 HTTP 客户端。"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from pypinyin import lazy_pinyin

DEFAULT_VOICE_CLONE_RESOURCE_ID = "seed-icl-2.0"
DEFAULT_VOICE_CLONE_URL = "https://openspeech.bytedance.com/api/v3/tts/voice_clone"
DEFAULT_VOICE_STATUS_URL = "https://openspeech.bytedance.com/api/v3/tts/get_voice"

_STATUS_LABELS = {
    0: "未找到",
    1: "训练中",
    2: "训练成功",
    3: "训练失败",
    4: "可用",
}
_READY_STATUSES = {2, 4}
_CUSTOM_SPEAKER_SENTINEL = "custom_speaker_id"


def custom_speaker_id_from_name(name: str) -> str:
    """把用户可读名称转成火山可用的自定义音色代号。"""
    raw = (name or "").strip()
    if not raw:
        raw = f"voice_{uuid4().hex[:8]}"
    pinyin = "_".join(lazy_pinyin(raw, errors="ignore"))
    slug = re.sub(r"[^a-z0-9]+", "_", pinyin.lower()).strip("_")
    if not slug:
        slug = f"voice_{uuid4().hex[:8]}"
    return "brufik_" + slug[:56]


@dataclass(frozen=True)
class DoubaoVoiceCloneConfig:
    app_key: str
    access_key: str
    resource_id: str = DEFAULT_VOICE_CLONE_RESOURCE_ID
    clone_url: str = DEFAULT_VOICE_CLONE_URL
    status_url: str = DEFAULT_VOICE_STATUS_URL
    timeout: int = 60

    def headers(self) -> dict[str, str]:
        if not self.app_key:
            raise ValueError("请先配置火山语音 App ID")
        if not self.access_key:
            raise ValueError("请先配置火山语音 Access Token")
        return {
            "Content-Type": "application/json",
            "X-Api-App-Key": self.app_key,
            "X-Api-Access-Key": self.access_key,
            "X-Api-Resource-Id": self.resource_id or DEFAULT_VOICE_CLONE_RESOURCE_ID,
            "X-Api-Request-Id": uuid4().hex,
        }


@dataclass(frozen=True)
class DoubaoVoiceCloneResult:
    speaker_id: str
    status: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    model_type: int | None = None

    @property
    def ready(self) -> bool:
        return self.status in _READY_STATUSES

    @property
    def status_label(self) -> str:
        if self.status is None:
            return "未知"
        return _STATUS_LABELS.get(self.status, f"未知({self.status})")

    def as_payload(self) -> dict[str, Any]:
        return {
            "speaker_id": self.speaker_id,
            "status": self.status,
            "status_label": self.status_label,
            "ready": self.ready,
            "model_type": self.model_type,
            "raw": self.raw,
        }


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str], *, timeout: int) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 固定火山 OpenSpeech HTTPS 地址
            raw = resp.read()
            http_status = int(getattr(resp, "status", 200) or 200)
    except HTTPError as exc:
        raw = exc.read()
        msg = raw.decode("utf-8", errors="replace") if raw else str(exc)
        raise RuntimeError(f"火山声音复刻请求失败 HTTP {exc.code}: {msg}") from exc
    except URLError as exc:
        raise RuntimeError(f"火山声音复刻请求失败: {exc.reason}") from exc

    text = raw.decode("utf-8", errors="replace") if raw else "{}"
    try:
        data = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"火山声音复刻响应不是 JSON: {text[:300]}") from exc
    if http_status >= 400:
        raise RuntimeError(f"火山声音复刻请求失败 HTTP {http_status}: {text[:300]}")
    if not isinstance(data, dict):
        raise RuntimeError("火山声音复刻响应格式异常")
    _raise_for_business_error(data)
    return data


def _raise_for_business_error(data: dict[str, Any]) -> None:
    code = data.get("status_code", data.get("code"))
    base_resp = data.get("BaseResp")
    if code is None and isinstance(base_resp, dict):
        code = base_resp.get("StatusCode")
    if code in (None, 0, 20000000, "0", "20000000"):
        return
    message = (
        data.get("status_message")
        or data.get("message")
        or (base_resp.get("StatusMessage") if isinstance(base_resp, dict) else "")
        or "unknown"
    )
    raise RuntimeError(f"火山声音复刻返回错误 {code}: {message}")


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _status_item(data: dict[str, Any], speaker_id: str) -> dict[str, Any]:
    rows = data.get("speaker_status")
    if isinstance(rows, list) and rows:
        for row in rows:
            if isinstance(row, dict) and str(row.get("speaker_id") or "") == speaker_id:
                return row
        first = rows[0]
        if isinstance(first, dict):
            return first
    return data


def _result_from_response(data: dict[str, Any], speaker_id: str) -> DoubaoVoiceCloneResult:
    item = _status_item(data, speaker_id)
    resolved_speaker = str(
        item.get("speaker_id")
        or item.get("custom_speaker_id")
        or data.get("speaker_id")
        or data.get("custom_speaker_id")
        or speaker_id
    ).strip()
    if resolved_speaker == _CUSTOM_SPEAKER_SENTINEL:
        resolved_speaker = str(
            item.get("custom_speaker_id") or data.get("custom_speaker_id") or speaker_id
        ).strip()
    return DoubaoVoiceCloneResult(
        speaker_id=resolved_speaker,
        status=_int_or_none(item.get("status")),
        model_type=_int_or_none(item.get("model_type")),
        raw=data,
    )


def clone_doubao_voice(
    cfg: DoubaoVoiceCloneConfig,
    *,
    audio_bytes: bytes,
    audio_format: str,
    language: int = 0,
    display_name: str = "",
    speaker_id: str = "",
    custom_speaker_id: str = "",
    prompt_text: str = "",
) -> DoubaoVoiceCloneResult:
    clean_speaker = (speaker_id or "").strip()
    clean_custom_speaker = (custom_speaker_id or "").strip()
    if not clean_speaker:
        clean_custom_speaker = clean_custom_speaker or custom_speaker_id_from_name(display_name)
        clean_speaker = _CUSTOM_SPEAKER_SENTINEL
    if not audio_bytes:
        raise ValueError("请上传训练音频")
    clean_format = (audio_format or "").strip().lower()
    if not clean_format:
        raise ValueError("无法识别音频格式")

    audio: dict[str, Any] = {
        "data": base64.b64encode(audio_bytes).decode("ascii"),
        "format": clean_format,
    }
    if prompt_text:
        audio["text"] = prompt_text.strip()
    payload: dict[str, Any] = {
        "speaker_id": clean_speaker,
        "audio": audio,
        "language": int(language),
    }
    resolved_speaker_id = clean_speaker
    if clean_speaker == _CUSTOM_SPEAKER_SENTINEL:
        payload["custom_speaker_id"] = clean_custom_speaker
        resolved_speaker_id = clean_custom_speaker
    if display_name:
        payload["display_name"] = display_name.strip()

    data = _post_json(cfg.clone_url, payload, cfg.headers(), timeout=cfg.timeout)
    return _result_from_response(data, resolved_speaker_id)


def get_doubao_voice_clone_status(
    cfg: DoubaoVoiceCloneConfig, speaker_id: str
) -> DoubaoVoiceCloneResult:
    clean_speaker = (speaker_id or "").strip()
    if not clean_speaker:
        raise ValueError("请填写声音复刻音色 ID")
    data = _post_json(
        cfg.status_url,
        {"speaker_id": clean_speaker},
        cfg.headers(),
        timeout=cfg.timeout,
    )
    return _result_from_response(data, clean_speaker)
