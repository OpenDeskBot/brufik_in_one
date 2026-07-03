from __future__ import annotations

import asyncio
import base64
import copy
import json
import logging
import os
import time
import uuid
from typing import TYPE_CHECKING, Any, Optional

from deskbot_server.auto_reply import get_asr_voice_auto_reply_enabled
from deskbot_server.pb_idle_dispatch import get_pb_idle_auto_dispatch_enabled
from deskbot_server.debug_prefs_store import (
    debug_prefs_snapshot,
    get_camera_servo_auto_mode,
    normalize_camera_servo_auto_mode,
    persist_asr_auto_reply,
    persist_pb_idle_auto_dispatch,
    persist_camera_servo_auto_mode,
)
from deskbot_server.constants import FACE_DESIGN_FILE, SERVO_CFG_FILE
from deskbot_server.device_data import resolve_json_path
from deskbot_server.face_expr_scenes_store import (
    design_frames_to_pb_chain,
    find_design_scene_by_name,
    load_face_expr_scenes_file,
)
from deskbot_server.servo_config_store import (
    load_servo_cfg_file,
    normalize_servo_document,
    save_servo_cfg_file,
)
from deskbot_server.pb.scenes import (
    _pb_scene_entry_by_name,
    _pb_scene_keys_sorted,
    _prepare_pb_scene_chain_frames,
)
from deskbot_server.pb.servo_pcm import attach_pb_device_hints_from_config
from deskbot_server.pb.shapes import (
    PB_ACTION_APPEND,
    PB_ACTION_DEFAULT,
    PB_ACTION_REPLACE,
    PB_LEVEL_DEBUG,
    apply_pb_dispatch_fields,
)
from deskbot_server.util import _extract_device_id, _parse_query
from deskbot_server.ws.registry import DeviceRegistry

if TYPE_CHECKING:
    from deskbot_server.application.chat_service import ChatService
    from deskbot_server.ws.asr_chat_hub import AsrChatHub
    from deskbot_server.ws.device_pipeline import DevicePipelineBroker

logger = logging.getLogger("deskbot-server")


def _config_device_id(qargs: dict, body: object = None) -> Optional[str]:
    dev = (_extract_device_id(qargs) or "").strip()
    if not dev and isinstance(body, dict):
        dev = str(body.get("device_id") or "").strip()
    return dev or None


def _registry_channels(registry: DeviceRegistry, device_id: str) -> dict[str, int]:
    for row in registry.snapshot():
        if row.get("device_id") == device_id:
            ch = row.get("channels")
            return dict(ch) if isinstance(ch, dict) else {}
    return {}


def _build_http_request_handler(
    device_pipeline_broker: DevicePipelineBroker,
    registry: DeviceRegistry,
    *,
    asr_chat_hub: "AsrChatHub",
    chat: "ChatService",
):
    """在同端口之上提供极简 HTTP 接口（不新增依赖）。

    - GET /api/devices：返回当前注册的设备列表（源自 DeviceRegistry）
    - GET /api/pipeline_recent?device_id=xxx&limit=100：返回滚动窗口内的事件
    - GET /api/device_servo?device_id=…&dyaw=…&dpitch=…&ms=…&xm=…&ym=…&action=…&level=…：向该设备
      已连接的 ``/asr_chat`` 下发仅含 ``servo`` 的 **单片** ``pb_single``。``xm``/``ym`` 缺省
      ``0``（绝对，x/y 钳位 0–180）；``xm=ym=1`` 时 dyaw/dpitch 为相对增量（±90）。可选 ``action``：``replace`` / ``append`` / ``default``，缺省
      ``replace``。可选 ``level``：``0``–``3``，缺省 ``3``（调试态）。可选 ``with_scene`` / ``append_scene``：
      与 ``pb_single`` 在**同一条** ``device_pb_only`` 链锁内**原子**顺序下发该场景
      的 ``pb_start``→…→``pb_end``（与 ``/api/device_pb_scene`` 同源 JSON，无 PCM），
      用于注视/跟随舵机与 ``happy_smile`` 同批入队。下位机约定：仅一帧时用
      ``pb_single``；多帧须以 ``pb_start`` 开头、``pb_end`` 收尾，勿单发 ``pb_end`` 冒充单片。
    - GET /api/device_pb_scenes：列出 ``data/deskbot-face.json`` 中 ``emotions`` 的场景 name。
    - GET /api/device_face_catalog：列出 ``deskbot-face.json`` 音素与情绪摘要（调试页）。
    - GET /api/device_face_play?device_id=…&kind=phoneme|emotion&name=…：向设备下发对应表情链（无音频）。
    - GET /api/device_pb_scene?device_id=…&scene=<id>：按 ``deskbot-face.json`` 向该设备
      ``/asr_chat`` **顺序**下发 ``pb_start`` → ``pb_chunk``* → ``pb_end``（含 ``anim``+``servo``，无音频）。
      ``scene`` 与文件中的 key **不区分大小写**。
    - GET /api/asr_auto_reply：返回 ``{"ok", "enabled"}`` 是否对 ``/asr_chat`` 执行 LLM+TTS；
      带 ``?enabled=1|0|true|false`` 时同时写入开关（调试页「启用自动应答」）。
    - GET/POST /api/device_tts：``device_id`` + ``text``，跳过 LLM 直接音素 TTS 并下发 pb；
      可选 ``scene``：与 TTS 在同一条 pb 链锁内顺序追加该场景帧（先语音后场景）。
    - GET/POST /api/servo_config：读取/写入 ``data/servo.json`` 舵机限位/反向与 ``presets`` 动作预设。
    - POST /api/device_pb_anim：向设备下发仅含 ``anim`` 的单片 ``pb_single``（调试口型预览）。
    - GET /health：存活探针
    - 其它 /api/* 返回 404；非 WS 升级请求才会进入该分支。
    """
    from websockets.datastructures import Headers
    from websockets.http11 import Response

    def _cors_headers() -> Headers:
        headers = Headers()
        headers["Access-Control-Allow-Origin"] = "*"
        headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
        headers["Cache-Control"] = "no-store"
        return headers

    def _json_resp(status: int, payload: object) -> Response:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = _cors_headers()
        headers["Content-Type"] = "application/json; charset=utf-8"
        headers["Content-Length"] = str(len(body))
        reason = {200: "OK", 404: "Not Found", 400: "Bad Request"}.get(
            status, "OK"
        )
        return Response(status, reason, headers, body)

    def _no_content() -> Response:
        headers = _cors_headers()
        headers["Content-Length"] = "0"
        return Response(204, "No Content", headers, b"")

    def _peer_from_conn(connection) -> str:
        try:
            peer = getattr(connection, "remote_address", None)
            if peer and isinstance(peer, tuple) and len(peer) >= 2:
                return f"{peer[0]}:{peer[1]}"
        except Exception:
            pass
        return "?"

    async def handler(connection, request):
        upgrade = (request.headers.get("Upgrade") or "").strip().lower()
        if upgrade == "websocket":
            return None

        raw_path = request.path or ""
        path_only, _, query = raw_path.partition("?")
        qargs = _parse_query(query)
        peer = _peer_from_conn(connection)
        method = (getattr(request, "method", None) or "GET").upper()
        if method == "OPTIONS" and path_only.startswith("/api/"):
            return _no_content()

        if path_only == "/health":
            logger.info("[HTTP] GET /health peer=%s -> 200", peer)
            return _json_resp(200, {"ok": True})

        api_auth = None
        if path_only.startswith("/api/"):
            from deskbot_server.auth.api_key_service import QuotaExceededError
            from deskbot_server.ws.api_key_gate import QUOTA_MESSAGE, http_require_api_key

            try:
                api_auth = http_require_api_key(qargs, request.headers)
            except QuotaExceededError:
                return _json_resp(
                    429,
                    {
                        "ok": False,
                        "error": "quota_exhausted",
                        "message": QUOTA_MESSAGE,
                    },
                )
            except PermissionError:
                return _json_resp(
                    401,
                    {"ok": False, "error": "api_key_required", "message": "缺少或无效的 API Key"},
                )

        def _device_access_denied(device_id: str | None):
            from deskbot_server.ws.api_key_gate import http_require_device_access

            try:
                http_require_device_access(api_auth, device_id)
            except PermissionError:
                return _json_resp(
                    403,
                    {"ok": False, "error": "forbidden_device", "message": "无权操作该设备"},
                )
            return None

        if path_only == "/api/devices":
            snap = registry.snapshot()
            if api_auth is not None and api_auth.user_id:
                from deskbot_server.auth.device_service import device_ids_for_user

                allowed = device_ids_for_user(api_auth.user_id)
                snap = [d for d in snap if str(d.get("device_id") or "") in allowed]
            device_ids = [d.get("device_id") for d in snap]
            logger.info(
                "[HTTP] GET /api/devices peer=%s -> %d 台设备 device_ids=%s",
                peer,
                len(snap),
                device_ids,
            )
            return _json_resp(
                200,
                {
                    "devices": snap,
                    "t": time.time(),
                },
            )

        if path_only == "/api/asr_auto_reply":
            raw_e = qargs.get("enabled")
            if raw_e is None:
                logger.info(
                    "[HTTP] GET /api/asr_auto_reply -> enabled=%s peer=%s",
                    get_asr_voice_auto_reply_enabled(),
                    peer,
                )
            if raw_e is not None:
                se = str(raw_e).strip().lower()
                if se in ("1", "true", "yes", "on"):
                    persist_asr_auto_reply(True)
                elif se in ("0", "false", "no", "off"):
                    persist_asr_auto_reply(False)
                else:
                    return _json_resp(
                        400,
                        {
                            "ok": False,
                            "error": "invalid enabled; use 1/0 or true/false",
                        },
                    )
                logger.info(
                    "[HTTP] /api/asr_auto_reply set enabled=%s peer=%s (已写入 config.yaml)",
                    get_asr_voice_auto_reply_enabled(),
                    peer,
                )
            return _json_resp(
                200,
                {"ok": True, "enabled": get_asr_voice_auto_reply_enabled()},
            )

        if path_only == "/api/pb_idle_auto_dispatch":
            raw_e = qargs.get("enabled")
            if raw_e is None:
                logger.info(
                    "[HTTP] GET /api/pb_idle_auto_dispatch -> enabled=%s peer=%s",
                    get_pb_idle_auto_dispatch_enabled(),
                    peer,
                )
            if raw_e is not None:
                se = str(raw_e).strip().lower()
                if se in ("1", "true", "yes", "on"):
                    persist_pb_idle_auto_dispatch(True)
                elif se in ("0", "false", "no", "off"):
                    persist_pb_idle_auto_dispatch(False)
                else:
                    return _json_resp(
                        400,
                        {
                            "ok": False,
                            "error": "invalid enabled; use 1/0 or true/false",
                        },
                    )
                logger.info(
                    "[HTTP] /api/pb_idle_auto_dispatch set enabled=%s peer=%s (已写入 config.yaml)",
                    get_pb_idle_auto_dispatch_enabled(),
                    peer,
                )
            return _json_resp(
                200,
                {"ok": True, "enabled": get_pb_idle_auto_dispatch_enabled()},
            )

        if path_only == "/api/camera_servo_auto_mode":
            raw_m = qargs.get("mode")
            if raw_m is None:
                logger.info(
                    "[HTTP] GET /api/camera_servo_auto_mode -> mode=%r peer=%s",
                    get_camera_servo_auto_mode(),
                    peer,
                )
            else:
                norm = normalize_camera_servo_auto_mode(raw_m)
                if str(raw_m).strip() and not norm and str(raw_m).strip().lower() not in (
                    "",
                    "off",
                    "none",
                ):
                    return _json_resp(
                        400,
                        {
                            "ok": False,
                            "error": "invalid mode; use follow, follow_frontal, gaze or empty",
                        },
                    )
                if str(raw_m).strip().lower() in ("", "off", "none"):
                    norm = persist_camera_servo_auto_mode("")
                else:
                    norm = persist_camera_servo_auto_mode(norm)
                logger.info(
                    "[HTTP] /api/camera_servo_auto_mode set mode=%r peer=%s (已写入 config.yaml)",
                    norm,
                    peer,
                )
            return _json_resp(
                200,
                {"ok": True, "mode": get_camera_servo_auto_mode()},
            )

        if path_only == "/api/debug_prefs":
            raw_ar = qargs.get("asr_auto_reply")
            raw_mode = qargs.get("camera_servo_auto_mode")
            if raw_ar is None and raw_mode is None:
                return _json_resp(200, {"ok": True, **debug_prefs_snapshot()})
            if raw_ar is not None:
                se = str(raw_ar).strip().lower()
                if se in ("1", "true", "yes", "on"):
                    persist_asr_auto_reply(True)
                elif se in ("0", "false", "no", "off"):
                    persist_asr_auto_reply(False)
                else:
                    return _json_resp(
                        400,
                        {"ok": False, "error": "invalid asr_auto_reply"},
                    )
            if raw_mode is not None:
                if str(raw_mode).strip().lower() in ("", "off", "none"):
                    persist_camera_servo_auto_mode("")
                else:
                    norm = normalize_camera_servo_auto_mode(raw_mode)
                    if not norm:
                        return _json_resp(
                            400,
                            {"ok": False, "error": "invalid camera_servo_auto_mode"},
                        )
                    persist_camera_servo_auto_mode(norm)
            return _json_resp(200, {"ok": True, **debug_prefs_snapshot()})

        if path_only == "/api/pipeline_recent":
            dev = _extract_device_id(qargs)
            denied = _device_access_denied(dev)
            if denied is not None:
                return denied
            try:
                limit = int(qargs.get("limit") or str(device_pipeline_broker.max_events))
            except ValueError:
                limit = device_pipeline_broker.max_events
            limit = max(1, min(device_pipeline_broker.max_events, limit))
            items = device_pipeline_broker.snapshot_events(dev, limit)
            logger.info(
                "[HTTP] GET /api/pipeline_recent peer=%s device_id=%s limit=%d -> %d 条",
                peer,
                dev,
                limit,
                len(items),
            )
            return _json_resp(
                200,
                {
                    "items": items,
                    "device_id": dev,
                    "limit": limit,
                    "max_events": device_pipeline_broker.max_events,
                    "t": time.time(),
                },
            )

        if path_only == "/api/device_servo":
            dev = (qargs.get("device_id") or "").strip()
            if not dev:
                return _json_resp(
                    400,
                    {"error": "missing device_id", "t": time.time()},
                )
            denied = _device_access_denied(dev)
            if denied is not None:
                return denied
            try:
                dyaw = float(qargs.get("dyaw") or 0.0)
                dpitch = float(qargs.get("dpitch") or 0.0)
            except (TypeError, ValueError):
                return _json_resp(
                    400,
                    {"error": "invalid dyaw or dpitch", "t": time.time()},
                )
            try:
                ms = int(qargs.get("ms") or 400)
            except (TypeError, ValueError):
                ms = 400
            ms = max(50, min(ms, 10_000))
            try:
                # 固定镜头默认绝对定位；显式传 xm=1 时为相对增量
                xm = int(qargs.get("xm") if qargs.get("xm") is not None else 0)
            except (TypeError, ValueError):
                return _json_resp(
                    400,
                    {"error": "invalid xm (use 0=absolute|1=relative)", "t": time.time()},
                )
            try:
                ym = int(qargs.get("ym") if qargs.get("ym") is not None else xm)
            except (TypeError, ValueError):
                return _json_resp(
                    400,
                    {"error": "invalid ym (use 0=absolute|1=relative)", "t": time.time()},
                )
            if xm == 0:
                ix = int(round(max(0.0, min(180.0, dyaw))))
            else:
                ix = int(round(max(-90.0, min(90.0, dyaw))))
            if ym == 0:
                iy = int(round(max(0.0, min(180.0, dpitch))))
            else:
                iy = int(round(max(-90.0, min(90.0, dpitch))))
            if xm not in (0, 1) or ym not in (0, 1):
                return _json_resp(
                    400,
                    {
                        "error": "invalid xm/ym (debug UI: 0=absolute, 1=relative)",
                        "t": time.time(),
                    },
                )
            act = (qargs.get("action") or PB_ACTION_REPLACE).strip().lower()
            if act not in (PB_ACTION_REPLACE, PB_ACTION_APPEND, PB_ACTION_DEFAULT):
                return _json_resp(
                    400,
                    {
                        "error": "invalid action (use replace|append|default)",
                        "t": time.time(),
                    },
                )
            try:
                pb_level = int(qargs.get("level", PB_LEVEL_DEBUG))
            except (TypeError, ValueError):
                pb_level = -1
            if pb_level not in (0, 1, 2, 3):
                return _json_resp(
                    400,
                    {
                        "error": "invalid level (use 0|1|2|3)",
                        "t": time.time(),
                    },
                )
            req_id = uuid.uuid4().hex[:16]
            payload = {
                "type": "pb_single",
                "req": req_id,
                "idx": 0,
                "chunk_ms": ms,
                "pb_ver": 2,
                "action": act,
                "level": pb_level,
                "servo": [
                    {
                        "xm": xm,
                        "ym": ym,
                        "x": ix,
                        "y": iy,
                        "ms": ms,
                    }
                ],
            }
            attach_pb_device_hints_from_config(payload)

            with_scene = (qargs.get("with_scene") or qargs.get("append_scene") or "").strip()
            tail_frames: Optional[list[dict]] = None
            scene_req: Optional[str] = None
            if with_scene:
                if _pb_scene_entry_by_name({}, with_scene):
                    scene_req = uuid.uuid4().hex[:16]
                    tail_frames = _prepare_pb_scene_chain_frames(
                        with_scene, runtime_req=scene_req
                    )
                    if not tail_frames:
                        tail_frames = None
                        scene_req = None
                else:
                    logger.warning(
                        "[/api/device_servo] with_scene=%r 未知或空帧，仅下发 pb_single device_id=%s",
                        with_scene,
                        dev,
                    )

            try:
                logger.info(
                    "[/api/device_servo] 发往 device_id=%s（/asr_chat WebSocket）文本帧: %s%s",
                    dev,
                    json.dumps(payload, ensure_ascii=False),
                    f" +scene={with_scene!r} frames={len(tail_frames)}" if tail_frames else "",
                )
                if tail_frames:
                    n = await asr_chat_hub.send_pb_single_then_chain_ordered(
                        dev, payload, tail_frames
                    )
                else:
                    n = await asr_chat_hub.send(dev, payload)
            except Exception:
                logger.exception(
                    "[HTTP] /api/device_servo 下发异常 device_id=%s", dev
                )
                n = 0
            logger.info(
                "[HTTP] GET /api/device_servo peer=%s device_id=%s "
                "dyaw=%s dpitch=%s xm=%d ym=%d action=%s level=%d -> pb_single ix=%d iy=%d ms=%d delivered=%d%s",
                peer,
                dev,
                dyaw,
                dpitch,
                xm,
                ym,
                act,
                pb_level,
                ix,
                iy,
                ms,
                n,
                f" with_scene={with_scene!r}" if with_scene else "",
            )
            return _json_resp(
                200,
                {
                    "ok": True,
                    "device_id": dev,
                    "type": "pb_single",
                    "action": act,
                    "level": pb_level,
                    "servo": payload["servo"],
                    "req": req_id,
                    "delivered": n,
                    "with_scene": with_scene or None,
                    "scene_req": scene_req,
                    "scene_frames": len(tail_frames) if tail_frames else 0,
                    "t": time.time(),
                },
            )

        if path_only == "/api/device_pb_scenes":
            keys = _pb_scene_keys_sorted(None)
            logger.info(
                "[HTTP] GET /api/device_pb_scenes peer=%s -> %d scene(s)",
                peer,
                len(keys),
            )
            return _json_resp(
                200,
                {
                    "ok": True,
                    "scenes": keys,
                    "file": os.path.basename(FACE_DESIGN_FILE),
                    "t": time.time(),
                },
            )

        if path_only == "/api/device_face_catalog":
            from deskbot_server.face_design_store import build_face_expression_catalog

            try:
                catalog = build_face_expression_catalog()
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                return _json_resp(
                    500,
                    {"ok": False, "error": str(exc), "t": time.time()},
                )
            logger.info(
                "[HTTP] GET /api/device_face_catalog peer=%s phonemes=%d emotions=%d",
                peer,
                len(catalog.get("phonemes") or []),
                len(catalog.get("emotions") or []),
            )
            return _json_resp(
                200,
                {
                    "ok": True,
                    "phonemes": catalog.get("phonemes") or [],
                    "emotions": catalog.get("emotions") or [],
                    "file": os.path.basename(FACE_DESIGN_FILE),
                    "t": time.time(),
                },
            )

        if path_only == "/api/device_face_play":
            if method != "GET":
                return _json_resp(
                    405,
                    {"ok": False, "error": "method not allowed", "t": time.time()},
                )
            from deskbot_server.face_design_store import (
                _load_face_design_cached,
                build_face_expression_catalog,
                ensure_face_design_file,
                resolve_face_expression,
            )

            dev = (qargs.get("device_id") or "").strip()
            kind_q = (qargs.get("kind") or "").strip().lower()
            name_q = (qargs.get("name") or qargs.get("scene") or "").strip()
            if not dev:
                return _json_resp(
                    400,
                    {"ok": False, "error": "missing device_id", "t": time.time()},
                )
            denied = _device_access_denied(dev)
            if denied is not None:
                return denied
            if kind_q not in ("phoneme", "emotion"):
                return _json_resp(
                    400,
                    {"ok": False, "error": "kind must be phoneme or emotion", "t": time.time()},
                )
            if not name_q:
                return _json_resp(
                    400,
                    {"ok": False, "error": "missing name", "t": time.time()},
                )
            try:
                doc = _load_face_design_cached(device_id=dev)
                if not isinstance(doc, dict):
                    doc = ensure_face_design_file(device_id=dev)
                ent = resolve_face_expression(doc, kind=kind_q, name=name_q)
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                return _json_resp(
                    500,
                    {"ok": False, "error": str(exc), "t": time.time()},
                )
            if ent is None:
                catalog = build_face_expression_catalog(device_id=dev)
                if kind_q == "phoneme":
                    valid = [
                        str(x.get("name") or "")
                        for x in catalog.get("phonemes") or []
                        if isinstance(x, dict) and x.get("name")
                    ]
                else:
                    valid = [
                        str(x.get("name") or "")
                        for x in catalog.get("emotions") or []
                        if isinstance(x, dict) and x.get("name")
                    ]
                return _json_resp(
                    400,
                    {
                        "ok": False,
                        "error": f"unknown {kind_q}: {name_q!r}",
                        "valid_names": sorted(set(valid)),
                        "t": time.time(),
                    },
                )
            req_id = uuid.uuid4().hex[:16]
            pairs = design_frames_to_pb_chain(ent.get("frames") or [], runtime_req=req_id)
            if not pairs:
                return _json_resp(
                    500,
                    {"ok": False, "error": "empty frames", "t": time.time()},
                )
            chain = [msg for msg, _bins in pairs]
            binaries_per_frame = [list(_bins) for _msg, _bins in pairs]
            expr_name = str(ent.get("name") or name_q).strip()
            logger.info(
                "[HTTP] GET /api/device_face_play peer=%s device_id=%s kind=%s name=%s req=%s frames=%d",
                peer,
                dev,
                kind_q,
                expr_name,
                req_id,
                len(chain),
            )
            try:
                n = await asr_chat_hub.send_pb_chain_ordered(
                    dev, chain, binaries_per_frame=binaries_per_frame
                )
            except Exception:
                logger.exception(
                    "[HTTP] /api/device_face_play 下发异常 device_id=%s kind=%s name=%s",
                    dev,
                    kind_q,
                    expr_name,
                )
                n = 0
            hint = None
            channels: dict[str, int] = {}
            if n == 0:
                channels = _registry_channels(registry, dev)
                hint = (
                    "没有发往 WebSocket：该 device_id 当前无已连接的 /asr_chat。"
                    f"当前注册通道={channels or '无'}。"
                )
            return _json_resp(
                200,
                {
                    "ok": n > 0,
                    "device_id": dev,
                    "kind": kind_q,
                    "name": expr_name,
                    "req": req_id,
                    "frames": len(chain),
                    "delivered": n,
                    "hint": hint,
                    "error": hint if n == 0 else None,
                    "channels": channels if n == 0 else None,
                    "t": time.time(),
                },
            )

        if path_only == "/api/device_tts":
            from deskbot_server.application.chat_flow import run_device_tts_only
            from deskbot_server.infrastructure.ws.downlink_adapter import WsDownlinkAdapter
            from deskbot_server.ws.chat_turn import publish_ws_chat_turn

            dev = ""
            text = ""
            scene_q = ""
            if method == "POST":
                try:
                    raw_body = (getattr(request, "body", None) or b"").decode("utf-8")
                    payload = json.loads(raw_body) if raw_body.strip() else {}
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return _json_resp(
                        400,
                        {"ok": False, "error": "invalid JSON body", "t": time.time()},
                    )
                if isinstance(payload, dict):
                    dev = str(payload.get("device_id") or "").strip()
                    text = str(payload.get("text") or "").strip()
                    scene_q = str(payload.get("scene") or "").strip()
            else:
                dev = (qargs.get("device_id") or "").strip()
                text = (qargs.get("text") or "").strip()
                scene_q = (qargs.get("scene") or "").strip()
            if not dev:
                return _json_resp(
                    400,
                    {"ok": False, "error": "missing device_id", "t": time.time()},
                )
            denied = _device_access_denied(dev)
            if denied is not None:
                return denied
            if not text:
                return _json_resp(
                    400,
                    {"ok": False, "error": "missing text", "t": time.time()},
                )
            ws = await asr_chat_hub.first_ws(dev)
            if ws is None:
                return _json_resp(
                    200,
                    {
                        "ok": False,
                        "error": "device not connected on /asr_chat",
                        "device_id": dev,
                        "delivered": 0,
                        "hint": "请确认 ESP32 已用相同 device_id 连接 /asr_chat",
                        "t": time.time(),
                    },
                )
            req_id = uuid.uuid4().hex[:16]
            settings = chat.settings
            broker = device_pipeline_broker

            async def _device_tts_job() -> None:
                downlink = WsDownlinkAdapter(
                    ws,
                    settings=settings,
                    device_id=dev,
                    dp_broker=broker,
                )
                try:
                    turn = await run_device_tts_only(
                        downlink,
                        chat,
                        text,
                        request_id=req_id,
                        device_id=dev,
                        scenes=[scene_q] if scene_q else None,
                    )
                    await publish_ws_chat_turn(
                        broker,
                        registry,
                        dev,
                        source="device_tts",
                        asr_text=None,
                        t_asr_start=turn.t_llm_end,
                        t_asr_text=turn.t_llm_end,
                        flow=turn.as_dict(),
                        request_id=req_id,
                    )
                    ok = (turn.status or "ok") == "ok" and not turn.error
                    logger.info(
                        "[HTTP] /api/device_tts job done device_id=%s req=%s text=%r ok=%s err=%s",
                        dev,
                        req_id,
                        text[:120],
                        ok,
                        turn.error,
                    )
                except Exception:
                    logger.exception(
                        "[HTTP] /api/device_tts job failed device_id=%s req=%s",
                        dev,
                        req_id,
                    )

            asyncio.create_task(_device_tts_job())
            logger.info(
                "[HTTP] %s /api/device_tts accepted peer=%s device_id=%s req=%s text=%r",
                method,
                peer,
                dev,
                req_id,
                text[:120],
            )
            return _json_resp(
                200,
                {
                    "ok": True,
                    "accepted": True,
                    "device_id": dev,
                    "text": text,
                    "scene": scene_q or None,
                    "req": req_id,
                    "t": time.time(),
                },
            )

        if path_only == "/api/device_pb_scene":
            dev = (qargs.get("device_id") or "").strip()
            scene_q = (qargs.get("scene") or "").strip()
            if not dev:
                return _json_resp(
                    400,
                    {"error": "missing device_id", "t": time.time()},
                )
            denied = _device_access_denied(dev)
            if denied is not None:
                return denied
            if not scene_q:
                return _json_resp(
                    400,
                    {"error": "missing scene", "t": time.time()},
                )
            req_id = uuid.uuid4().hex[:16]
            ent = _pb_scene_entry_by_name({}, scene_q, device_id=dev)
            if ent is None:
                valid = _pb_scene_keys_sorted(None)
                return _json_resp(
                    400,
                    {
                        "error": f"unknown or empty scene: {scene_q!r}",
                        "valid_scenes": valid,
                        "t": time.time(),
                    },
                )
            pairs = design_frames_to_pb_chain(ent.get("frames") or [], runtime_req=req_id)
            if not pairs:
                return _json_resp(
                    500,
                    {"error": "empty frames", "t": time.time()},
                )
            frames = [msg for msg, _bins in pairs]
            binaries_per_frame = [list(_bins) for _msg, _bins in pairs]
            scene_log = str(ent.get("name") or scene_q).strip().lower()

            logger.info(
                "[HTTP] GET /api/device_pb_scene peer=%s device_id=%s scene=%s req=%s frames=%d",
                peer,
                dev,
                scene_log,
                req_id,
                len(frames),
            )
            try:
                n = await asr_chat_hub.send_pb_chain_ordered(
                    dev, frames, binaries_per_frame=binaries_per_frame
                )
            except Exception:
                logger.exception(
                    "[HTTP] /api/device_pb_scene 下发异常 device_id=%s scene=%s",
                    dev,
                    scene_log,
                )
                return _json_resp(
                    500,
                    {
                        "ok": False,
                        "error": "send failed (see server log)",
                        "device_id": dev,
                        "scene": scene_q,
                        "t": time.time(),
                    },
                )
            logger.info(
                "[/api/device_pb_scene] 已顺序下发 scene=%s device_id=%s req=%s frames=%d ws_sends=%d",
                scene_log,
                dev,
                req_id,
                len(frames),
                n,
            )
            hint = None
            if n == 0:
                hint = (
                    "没有发往 WebSocket：该 device_id 当前无已连接的 /asr_chat，"
                    "或连接已断开。请确认 ESP32 使用相同 device_id 登录 /asr_chat。"
                )
            return _json_resp(
                200,
                {
                    "ok": True,
                    "device_id": dev,
                    "scene": scene_q,
                    "req": req_id,
                    "frames": len(frames),
                    "delivered": n,
                    "hint": hint,
                    "t": time.time(),
                },
            )

        if path_only == "/api/servo_config":
            cfg_dev = _config_device_id(qargs)
            cfg_path = resolve_json_path(SERVO_CFG_FILE, cfg_dev)
            if method == "GET":
                try:
                    cfg = load_servo_cfg_file(device_id=cfg_dev)
                except (OSError, json.JSONDecodeError, ValueError) as exc:
                    logger.warning(
                        "[HTTP] GET /api/servo_config read failed peer=%s err=%s",
                        peer,
                        exc,
                    )
                    return _json_resp(
                        500,
                        {"ok": False, "error": str(exc), "t": time.time()},
                    )
                if cfg is None:
                    return _json_resp(
                        200,
                        {
                            "ok": True,
                            "exists": False,
                            "file": os.path.basename(cfg_path),
                            "device_id": cfg_dev,
                            "t": time.time(),
                        },
                    )
                logger.info(
                    "[HTTP] GET /api/servo_config peer=%s device_id=%s -> %s",
                    peer,
                    cfg_dev,
                    os.path.basename(cfg_path),
                )
                return _json_resp(
                    200,
                    {
                        "ok": True,
                        "exists": True,
                        "config": cfg,
                        "file": os.path.basename(cfg_path),
                        "device_id": cfg_dev,
                        "t": time.time(),
                    },
                )
            if method == "POST":
                try:
                    raw_body = (getattr(request, "body", None) or b"").decode("utf-8")
                    payload = json.loads(raw_body) if raw_body.strip() else {}
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return _json_resp(
                        400,
                        {"ok": False, "error": "invalid JSON body", "t": time.time()},
                    )
                cfg_dev = _config_device_id(qargs, payload if isinstance(payload, dict) else None)
                cfg_path = resolve_json_path(SERVO_CFG_FILE, cfg_dev)
                try:
                    cfg = normalize_servo_document(payload, require_presets=True)
                    save_servo_cfg_file(cfg, device_id=cfg_dev)
                except ValueError as exc:
                    return _json_resp(
                        400,
                        {"ok": False, "error": str(exc), "t": time.time()},
                    )
                except OSError as exc:
                    logger.warning(
                        "[HTTP] POST /api/servo_config write failed peer=%s err=%s",
                        peer,
                        exc,
                    )
                    return _json_resp(
                        500,
                        {"ok": False, "error": str(exc), "t": time.time()},
                    )
                logger.info(
                    "[HTTP] POST /api/servo_config peer=%s device_id=%s -> %s",
                    peer,
                    cfg_dev,
                    cfg_path,
                )
                return _json_resp(
                    200,
                    {
                        "ok": True,
                        "config": cfg,
                        "file": os.path.basename(cfg_path),
                        "device_id": cfg_dev,
                        "t": time.time(),
                    },
                )
            return _json_resp(
                405,
                {"ok": False, "error": "method not allowed", "t": time.time()},
            )

        if path_only == "/api/device_pb_anim":
            anim: Optional[dict[str, Any]] = None
            dev = ""
            chunk_ms = 500
            act = PB_ACTION_REPLACE
            pb_level = PB_LEVEL_DEBUG

            if method == "GET":
                dev = (qargs.get("device_id") or "").strip()
                anim_b64 = (qargs.get("anim_b64") or "").strip()
                if not dev:
                    return _json_resp(
                        400,
                        {"ok": False, "error": "missing device_id", "t": time.time()},
                    )
                if not anim_b64:
                    return _json_resp(
                        400,
                        {"ok": False, "error": "missing anim_b64", "t": time.time()},
                    )
                try:
                    pad = (-len(anim_b64)) % 4
                    anim = json.loads(
                        base64.b64decode(anim_b64 + ("=" * pad)).decode("utf-8")
                    )
                except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                    return _json_resp(
                        400,
                        {"ok": False, "error": f"invalid anim_b64: {exc}", "t": time.time()},
                    )
                try:
                    chunk_ms = int(qargs.get("chunk_ms", 500))
                except (TypeError, ValueError):
                    chunk_ms = 500
                act = str(qargs.get("action") or PB_ACTION_REPLACE).strip().lower()
                try:
                    pb_level = int(qargs.get("level", PB_LEVEL_DEBUG))
                except (TypeError, ValueError):
                    pb_level = PB_LEVEL_DEBUG
            elif method == "POST":
                try:
                    raw_body = (getattr(request, "body", None) or b"").decode("utf-8")
                    body = json.loads(raw_body) if raw_body.strip() else {}
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return _json_resp(
                        400,
                        {"ok": False, "error": "invalid JSON body", "t": time.time()},
                    )
                dev = str(body.get("device_id") or "").strip()
                anim = body.get("anim")
                try:
                    chunk_ms = int(body.get("chunk_ms", 500))
                except (TypeError, ValueError):
                    chunk_ms = 500
                act = str(body.get("action") or PB_ACTION_REPLACE).strip().lower()
                try:
                    pb_level = int(body.get("level", PB_LEVEL_DEBUG))
                except (TypeError, ValueError):
                    pb_level = PB_LEVEL_DEBUG
            else:
                return _json_resp(
                    405,
                    {"ok": False, "error": "method not allowed", "t": time.time()},
                )

            if not dev:
                return _json_resp(
                    400,
                    {"ok": False, "error": "missing device_id", "t": time.time()},
                )
            denied = _device_access_denied(dev)
            if denied is not None:
                return denied
            if not isinstance(anim, (dict, list)):
                return _json_resp(
                    400,
                    {"ok": False, "error": "anim must be array or object", "t": time.time()},
                )
            if isinstance(anim, dict):
                if not isinstance(anim.get("elements"), dict):
                    return _json_resp(
                        400,
                        {"ok": False, "error": "anim.elements required", "t": time.time()},
                    )
                anim_list = [{"elements": copy.deepcopy(anim["elements"]), "ms": chunk_ms}]
            else:
                anim_list = []
                for one in anim:
                    if not isinstance(one, dict) or not isinstance(one.get("elements"), dict):
                        return _json_resp(
                            400,
                            {"ok": False, "error": "anim[] items need elements", "t": time.time()},
                        )
                    try:
                        item_ms = int(one.get("ms") or chunk_ms)
                    except (TypeError, ValueError):
                        item_ms = chunk_ms
                    item: dict[str, Any] = {
                        "elements": copy.deepcopy(one["elements"]),
                        "ms": max(1, item_ms),
                    }
                    ph = str(one.get("phoneme") or "").strip()
                    if ph:
                        item["phoneme"] = ph
                    anim_list.append(item)
                if not anim_list:
                    return _json_resp(
                        400,
                        {"ok": False, "error": "anim[] must not be empty", "t": time.time()},
                    )
                chunk_ms = sum(int(x["ms"]) for x in anim_list)
            chunk_ms = max(50, min(10000, chunk_ms))
            if act not in (PB_ACTION_REPLACE, PB_ACTION_APPEND, PB_ACTION_DEFAULT):
                return _json_resp(
                    400,
                    {"ok": False, "error": "invalid action", "t": time.time()},
                )
            if pb_level not in (0, 1, 2, 3):
                return _json_resp(
                    400,
                    {"ok": False, "error": "invalid level", "t": time.time()},
                )
            req_id = uuid.uuid4().hex[:16]
            payload = {
                "type": "pb_single",
                "req": req_id,
                "idx": 0,
                "chunk_ms": chunk_ms,
                "pb_ver": 2,
                "action": act,
                "level": pb_level,
                "anim": anim_list,
            }
            attach_pb_device_hints_from_config(payload)
            logger.info(
                "[/api/device_pb_anim] 发往 device_id=%s（/asr_chat WebSocket）文本帧: %s",
                dev,
                json.dumps(payload, ensure_ascii=False),
            )
            try:
                n = await asr_chat_hub.send(dev, payload)
            except Exception:
                logger.exception(
                    "[HTTP] /api/device_pb_anim 下发异常 device_id=%s", dev
                )
                n = 0
            hint = None
            channels: dict[str, int] = {}
            if n == 0:
                channels = _registry_channels(registry, dev)
                hint = (
                    "没有发往 WebSocket：该 device_id 当前无已连接的 /asr_chat，"
                    "或连接已断开。pb 下发（表情/口型/场景/舵机）均需 ESP32 使用相同 device_id 登录 /asr_chat；"
                    f"当前注册通道={channels or '无'}。"
                )
                logger.warning(
                    "[HTTP] %s /api/device_pb_anim delivered=0 device_id=%s registry_channels=%s",
                    method,
                    dev,
                    channels or None,
                )
            logger.info(
                "[HTTP] %s /api/device_pb_anim peer=%s device_id=%s req=%s delivered=%d",
                method,
                peer,
                dev,
                req_id,
                n,
            )
            return _json_resp(
                200,
                {
                    "ok": n > 0,
                    "device_id": dev,
                    "req": req_id,
                    "delivered": n,
                    "hint": hint,
                    "error": hint if n == 0 else None,
                    "channels": channels if n == 0 else None,
                    "t": time.time(),
                },
            )

        if path_only == "/api/device_pb_expr_scene":
            if method != "GET":
                return _json_resp(
                    405,
                    {"ok": False, "error": "method not allowed", "t": time.time()},
                )
            dev = (qargs.get("device_id") or "").strip()
            scene_q = (qargs.get("scene") or qargs.get("name") or "").strip()
            if not dev:
                return _json_resp(
                    400,
                    {"ok": False, "error": "missing device_id", "t": time.time()},
                )
            denied = _device_access_denied(dev)
            if denied is not None:
                return denied
            if not scene_q:
                return _json_resp(
                    400,
                    {"ok": False, "error": "missing scene", "t": time.time()},
                )
            try:
                rows = load_face_expr_scenes_file(seed_if_missing=False, device_id=dev) or []
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                return _json_resp(
                    500,
                    {"ok": False, "error": str(exc), "t": time.time()},
                )
            ent = find_design_scene_by_name(rows, scene_q)
            if ent is None:
                valid = sorted({str(r.get("name") or "") for r in rows if r.get("name")})
                return _json_resp(
                    400,
                    {
                        "ok": False,
                        "error": f"unknown scene: {scene_q!r}",
                        "valid_scenes": valid,
                        "t": time.time(),
                    },
                )
            req_id = uuid.uuid4().hex[:16]
            pairs = design_frames_to_pb_chain(ent.get("frames") or [], runtime_req=req_id)
            if not pairs:
                return _json_resp(
                    500,
                    {"ok": False, "error": "empty frames", "t": time.time()},
                )
            chain = [msg for msg, _bins in pairs]
            binaries_per_frame = [list(_bins) for _msg, _bins in pairs]
            try:
                n = await asr_chat_hub.send_pb_chain_ordered(
                    dev, chain, binaries_per_frame=binaries_per_frame
                )
            except Exception:
                logger.exception(
                    "[HTTP] /api/device_pb_expr_scene 下发异常 device_id=%s scene=%s",
                    dev,
                    scene_q,
                )
                n = 0
            hint = None
            channels: dict[str, int] = {}
            if n == 0:
                channels = _registry_channels(registry, dev)
                hint = (
                    "没有发往 WebSocket：该 device_id 当前无已连接的 /asr_chat。"
                    f"当前注册通道={channels or '无'}。"
                )
            return _json_resp(
                200,
                {
                    "ok": n > 0,
                    "device_id": dev,
                    "scene": ent.get("name"),
                    "req": req_id,
                    "frames": len(chain),
                    "delivered": n,
                    "hint": hint,
                    "error": hint if n == 0 else None,
                    "channels": channels if n == 0 else None,
                    "t": time.time(),
                },
            )

        if path_only.startswith("/api/"):
            logger.warning(
                "[HTTP] 未知 API peer=%s path=%s -> 404", peer, path_only
            )
            return _json_resp(404, {"error": "not found", "path": path_only})

        logger.debug("[HTTP] 非 API 请求忽略 peer=%s path=%s", peer, path_only)
        return _no_content()

    return handler
