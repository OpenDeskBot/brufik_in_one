"""Web / 调试 Controller：后台 REST + ``/camera_view`` / ``/device_pipeline``。"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import logging
import os
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import JSONResponse
from websockets.exceptions import ConnectionClosed

from deskbot_server.constants import FACE_DESIGN_FILE, SERVO_CFG_FILE
from deskbot_server.controller.auth import (
    device_access_denied,
    require_api_auth,
    require_web_ws_pipeline_auth,
    require_web_ws_subscriber_auth,
)
from deskbot_server.controller.runtime import get_runtime
from deskbot_server.dao.debug_prefs_store import (
    debug_prefs_snapshot,
    get_camera_servo_auto_mode,
    get_live_service_enabled,
    normalize_camera_servo_auto_mode,
    persist_asr_auto_reply,
    persist_camera_servo_auto_mode,
    persist_live_service,
)
from deskbot_server.dao.face_expr_scenes_store import (
    design_frames_to_pb_chain,
    find_design_scene_by_name,
    load_face_expr_scenes_file,
)
from deskbot_server.dao.servo_config_store import (
    load_servo_cfg_file,
    normalize_servo_document,
    save_servo_cfg_file,
    servo_limits,
)
from deskbot_server.pb.scenes import _pb_scene_entry_by_name, _pb_scene_keys_sorted, _prepare_pb_scene_chain_frames
from deskbot_server.pb.servo_pcm import attach_pb_device_hints_from_config
from deskbot_server.pb.shapes import PB_ACTION_APPEND, PB_ACTION_DEFAULT, PB_ACTION_REPLACE, PB_LEVEL_DEBUG
from deskbot_server.service.auto_reply import get_asr_voice_auto_reply_enabled
from deskbot_server.service.camera_face_service import CameraFaceService
from deskbot_server.utils.async_helpers import run_blocking
from deskbot_server.utils.device_data import resolve_json_path
from deskbot_server.utils.util import _extract_device_id, _json_msg
from deskbot_server.utils.ws_utils import WsUtils
from deskbot_server.ws.device_pipeline import handle_device_pipeline
from deskbot_server.ws.registry import DeviceRegistry

logger = logging.getLogger("deskbot-server")

router = APIRouter(tags=["web"])


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


def _request_qargs(request: Request) -> dict:
    return {k.lower(): v for k, v in request.query_params.multi_items()}


def _request_peer(request: Request) -> str:
    if request.client is not None:
        return f"{request.client.host}:{request.client.port}"
    return "?"


@router.get("/health")
async def health() -> JSONResponse:
    logger.info("[HTTP] GET /health -> 200")
    return JSONResponse(status_code=200, content={"ok": True})


@router.get("/api/devices")
@require_api_auth
async def api_devices(request: Request) -> JSONResponse:
    from deskbot_server.controller.runtime import get_runtime

    registry = get_runtime().registry
    peer = _request_peer(request)
    snap = registry.snapshot()
    if request.state.api_auth is not None and request.state.api_auth.user_id:
        from deskbot_server.service.user_service import UserService

        allowed = UserService().device_ids_for_user(request.state.api_auth.user_id)
        snap = [d for d in snap if str(d.get("device_id") or "") in allowed]
    device_ids = [d.get("device_id") for d in snap]
    logger.info("[HTTP] GET /api/devices peer=%s -> %d 台设备 device_ids=%s", peer, len(snap), device_ids)
    return JSONResponse(status_code=200, content={"devices": snap, "t": time.time()})


@router.get("/api/asr_auto_reply")
@require_api_auth
async def api_asr_auto_reply(request: Request) -> JSONResponse:
    qargs = _request_qargs(request)
    peer = _request_peer(request)
    raw_e = qargs.get("enabled")
    if raw_e is None:
        logger.info("[HTTP] GET /api/asr_auto_reply -> enabled=%s peer=%s", get_asr_voice_auto_reply_enabled(), peer)
    if raw_e is not None:
        se = str(raw_e).strip().lower()
        if se in ("1", "true", "yes", "on"):
            persist_asr_auto_reply(True)
        elif se in ("0", "false", "no", "off"):
            persist_asr_auto_reply(False)
        else:
            return JSONResponse(
                status_code=400, content={"ok": False, "error": "invalid enabled; use 1/0 or true/false"}
            )
        logger.info(
            "[HTTP] /api/asr_auto_reply set enabled=%s peer=%s (已写入 config.yaml)",
            get_asr_voice_auto_reply_enabled(),
            peer,
        )
    return JSONResponse(status_code=200, content={"ok": True, "enabled": get_asr_voice_auto_reply_enabled()})


@router.get("/api/live_service")
@require_api_auth
async def api_live_service(request: Request) -> JSONResponse:
    qargs = _request_qargs(request)
    peer = _request_peer(request)
    raw_e = qargs.get("enabled")
    if raw_e is None:
        logger.info("[HTTP] GET /api/live_service -> enabled=%s peer=%s", get_live_service_enabled(), peer)
    if raw_e is not None:
        se = str(raw_e).strip().lower()
        if se in ("1", "true", "yes", "on"):
            persist_live_service(True)
        elif se in ("0", "false", "no", "off"):
            persist_live_service(False)
        else:
            return JSONResponse(
                status_code=400, content={"ok": False, "error": "invalid enabled; use 1/0 or true/false"}
            )
        logger.info(
            "[HTTP] /api/live_service set enabled=%s peer=%s (已写入 config.yaml)",
            get_live_service_enabled(),
            peer,
        )
    return JSONResponse(status_code=200, content={"ok": True, "enabled": get_live_service_enabled()})


@router.get("/api/camera_servo_auto_mode")
@require_api_auth
async def api_camera_servo_auto_mode(request: Request) -> JSONResponse:
    qargs = _request_qargs(request)
    peer = _request_peer(request)
    raw_m = qargs.get("mode")
    if raw_m is None:
        logger.info("[HTTP] GET /api/camera_servo_auto_mode -> mode=%r peer=%s", get_camera_servo_auto_mode(), peer)
    else:
        norm = normalize_camera_servo_auto_mode(raw_m)
        if str(raw_m).strip() and not norm and str(raw_m).strip().lower() not in ("", "off", "none"):
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "invalid mode; use follow, follow_frontal, gaze or empty"},
            )
        if str(raw_m).strip().lower() in ("", "off", "none"):
            norm = persist_camera_servo_auto_mode("")
        else:
            norm = persist_camera_servo_auto_mode(norm)
        logger.info("[HTTP] /api/camera_servo_auto_mode set mode=%r peer=%s (已写入 config.yaml)", norm, peer)
    return JSONResponse(status_code=200, content={"ok": True, "mode": get_camera_servo_auto_mode()})


@router.get("/api/debug_prefs")
@require_api_auth
async def api_debug_prefs(request: Request) -> JSONResponse:
    qargs = _request_qargs(request)
    raw_ar = qargs.get("asr_auto_reply")
    raw_live = qargs.get("live_service")
    raw_mode = qargs.get("camera_servo_auto_mode")
    if raw_ar is None and raw_live is None and raw_mode is None:
        return JSONResponse(status_code=200, content={"ok": True, **debug_prefs_snapshot()})
    if raw_ar is not None:
        se = str(raw_ar).strip().lower()
        if se in ("1", "true", "yes", "on"):
            persist_asr_auto_reply(True)
        elif se in ("0", "false", "no", "off"):
            persist_asr_auto_reply(False)
        else:
            return JSONResponse(status_code=400, content={"ok": False, "error": "invalid asr_auto_reply"})
    if raw_live is not None:
        se = str(raw_live).strip().lower()
        if se in ("1", "true", "yes", "on"):
            persist_live_service(True)
        elif se in ("0", "false", "no", "off"):
            persist_live_service(False)
        else:
            return JSONResponse(status_code=400, content={"ok": False, "error": "invalid live_service"})
    if raw_mode is not None:
        if str(raw_mode).strip().lower() in ("", "off", "none"):
            persist_camera_servo_auto_mode("")
        else:
            norm = normalize_camera_servo_auto_mode(raw_mode)
            if not norm:
                return JSONResponse(status_code=400, content={"ok": False, "error": "invalid camera_servo_auto_mode"})
            persist_camera_servo_auto_mode(norm)
    return JSONResponse(status_code=200, content={"ok": True, **debug_prefs_snapshot()})


@router.get("/api/pipeline_recent")
@require_api_auth
async def api_pipeline_recent(request: Request) -> JSONResponse:
    from deskbot_server.controller.runtime import get_runtime
    from deskbot_server.service.pipeline_service import PipelineService

    qargs = _request_qargs(request)
    peer = _request_peer(request)
    try:
        broker = PipelineService().broker
    except RuntimeError:
        broker = get_runtime().device_pipeline_broker
    dev = _extract_device_id(qargs)
    denied = device_access_denied(request.state.api_auth, dev)
    if denied is not None:
        return denied
    try:
        limit = int(qargs.get("limit") or str(broker.max_events))
    except ValueError:
        limit = broker.max_events
    limit = max(1, min(broker.max_events, limit))
    items = broker.snapshot_events(dev, limit)
    logger.info("[HTTP] GET /api/pipeline_recent peer=%s device_id=%s limit=%d -> %d 条", peer, dev, limit, len(items))
    return JSONResponse(
        status_code=200,
        content={"items": items, "device_id": dev, "limit": limit, "max_events": broker.max_events, "t": time.time()},
    )


@router.get("/api/device_servo")
@require_api_auth
async def api_device_servo(request: Request) -> JSONResponse:
    from deskbot_server.controller.runtime import get_runtime

    asr_chat_hub = get_runtime().asr_chat_hub
    qargs = _request_qargs(request)
    peer = _request_peer(request)
    dev = (qargs.get("device_id") or "").strip()
    if not dev:
        return JSONResponse(status_code=400, content={"error": "missing device_id", "t": time.time()})
    denied = device_access_denied(request.state.api_auth, dev)
    if denied is not None:
        return denied
    try:
        dyaw = float(qargs.get("dyaw") or 0.0)
        dpitch = float(qargs.get("dpitch") or 0.0)
    except (TypeError, ValueError):
        return JSONResponse(status_code=400, content={"error": "invalid dyaw or dpitch", "t": time.time()})
    try:
        ms = int(qargs.get("ms") or 400)
    except (TypeError, ValueError):
        ms = 400
    ms = max(50, min(ms, 10_000))
    try:
        # 固定镜头默认绝对定位；显式传 xm=1 时为相对增量
        xm = int(qargs.get("xm") if qargs.get("xm") is not None else 0)
    except (TypeError, ValueError):
        return JSONResponse(
            status_code=400, content={"error": "invalid xm (use 0=absolute|1=relative)", "t": time.time()}
        )
    try:
        ym = int(qargs.get("ym") if qargs.get("ym") is not None else xm)
    except (TypeError, ValueError):
        return JSONResponse(
            status_code=400, content={"error": "invalid ym (use 0=absolute|1=relative)", "t": time.time()}
        )
    lim = servo_limits(device_id=dev)
    if xm == 0:
        ix = int(round(max(float(lim["xMin"]), min(float(lim["xMax"]), dyaw))))
    else:
        ix = int(round(max(-90.0, min(90.0, dyaw))))
    if ym == 0:
        iy = int(round(max(float(lim["yMin"]), min(float(lim["yMax"]), dpitch))))
    else:
        iy = int(round(max(-90.0, min(90.0, dpitch))))
    if xm not in (0, 1) or ym not in (0, 1):
        return JSONResponse(
            status_code=400, content={"error": "invalid xm/ym (debug UI: 0=absolute, 1=relative)", "t": time.time()}
        )
    act = (qargs.get("action") or PB_ACTION_REPLACE).strip().lower()
    if act not in (PB_ACTION_REPLACE, PB_ACTION_APPEND, PB_ACTION_DEFAULT):
        return JSONResponse(
            status_code=400, content={"error": "invalid action (use replace|append|default)", "t": time.time()}
        )
    try:
        pb_level = int(qargs.get("level", PB_LEVEL_DEBUG))
    except (TypeError, ValueError):
        pb_level = -1
    if pb_level not in (0, 1, 2, 3):
        return JSONResponse(status_code=400, content={"error": "invalid level (use 0|1|2|3)", "t": time.time()})
    req_id = uuid.uuid4().hex[:16]
    payload = {
        "type": "pb_single",
        "req": req_id,
        "idx": 0,
        "chunk_ms": ms,
        "pb_ver": 2,
        "action": act,
        "level": pb_level,
        "servo": [{"xm": xm, "ym": ym, "x": ix, "y": iy, "ms": ms}],
    }
    attach_pb_device_hints_from_config(payload)

    with_scene = (qargs.get("with_scene") or qargs.get("append_scene") or "").strip()
    tail_frames: Optional[list[dict]] = None
    scene_req: Optional[str] = None
    if with_scene:
        if _pb_scene_entry_by_name({}, with_scene):
            scene_req = uuid.uuid4().hex[:16]
            tail_frames = _prepare_pb_scene_chain_frames(with_scene, runtime_req=scene_req)
            if not tail_frames:
                tail_frames = None
                scene_req = None
        else:
            logger.warning(
                "[/api/device_servo] with_scene=%r 未知或空帧，仅下发 pb_single device_id=%s", with_scene, dev
            )

    try:
        logger.info(
            "[/api/device_servo] 发往 device_id=%s（/asr_chat WebSocket）文本帧: %s%s",
            dev,
            json.dumps(payload, ensure_ascii=False),
            f" +scene={with_scene!r} frames={len(tail_frames)}" if tail_frames else "",
        )
        if tail_frames:
            n = await asr_chat_hub.send_pb_single_then_chain_ordered(dev, payload, tail_frames)
        else:
            n = await asr_chat_hub.send(dev, payload)
    except Exception:
        logger.exception("[HTTP] /api/device_servo 下发异常 device_id=%s", dev)
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
    return JSONResponse(
        status_code=200,
        content={
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


@router.get("/api/device_pb_scenes")
@require_api_auth
async def api_device_pb_scenes(request: Request) -> JSONResponse:
    peer = _request_peer(request)
    keys = _pb_scene_keys_sorted(None)
    logger.info("[HTTP] GET /api/device_pb_scenes peer=%s -> %d scene(s)", peer, len(keys))
    return JSONResponse(
        status_code=200,
        content={"ok": True, "scenes": keys, "file": os.path.basename(FACE_DESIGN_FILE), "t": time.time()},
    )


@router.get("/api/device_face_catalog")
@require_api_auth
async def api_device_face_catalog(request: Request) -> JSONResponse:
    from deskbot_server.dao.face_design_store import build_face_expression_catalog

    peer = _request_peer(request)
    try:
        catalog = build_face_expression_catalog()
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc), "t": time.time()})
    logger.info(
        "[HTTP] GET /api/device_face_catalog peer=%s phonemes=%d emotions=%d",
        peer,
        len(catalog.get("phonemes") or []),
        len(catalog.get("emotions") or []),
    )
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "phonemes": catalog.get("phonemes") or [],
            "emotions": catalog.get("emotions") or [],
            "file": os.path.basename(FACE_DESIGN_FILE),
            "t": time.time(),
        },
    )


@router.get("/api/device_face_play")
@require_api_auth
async def api_device_face_play(request: Request) -> JSONResponse:
    if request.method.upper() != "GET":
        return JSONResponse(status_code=405, content={"ok": False, "error": "method not allowed", "t": time.time()})
    from deskbot_server.controller.runtime import get_runtime
    from deskbot_server.dao.face_design_store import (
        _load_face_design_cached,
        build_face_expression_catalog,
        ensure_face_design_file,
        resolve_face_expression,
    )

    rt = get_runtime()
    asr_chat_hub = rt.asr_chat_hub
    registry = rt.registry
    qargs = _request_qargs(request)
    peer = _request_peer(request)
    dev = (qargs.get("device_id") or "").strip()
    kind_q = (qargs.get("kind") or "").strip().lower()
    name_q = (qargs.get("name") or qargs.get("scene") or "").strip()
    if not dev:
        return JSONResponse(status_code=400, content={"ok": False, "error": "missing device_id", "t": time.time()})
    denied = device_access_denied(request.state.api_auth, dev)
    if denied is not None:
        return denied
    if kind_q not in ("phoneme", "emotion"):
        return JSONResponse(
            status_code=400, content={"ok": False, "error": "kind must be phoneme or emotion", "t": time.time()}
        )
    if not name_q:
        return JSONResponse(status_code=400, content={"ok": False, "error": "missing name", "t": time.time()})
    try:
        doc = await run_blocking(_load_face_design_cached, device_id=dev)
        if not isinstance(doc, dict):
            doc = await run_blocking(ensure_face_design_file, device_id=dev)
        ent = resolve_face_expression(doc, kind=kind_q, name=name_q)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc), "t": time.time()})
    if ent is None:
        catalog = build_face_expression_catalog(device_id=dev)
        if kind_q == "phoneme":
            valid = [
                str(x.get("name") or "") for x in catalog.get("phonemes") or [] if isinstance(x, dict) and x.get("name")
            ]
        else:
            valid = [
                str(x.get("name") or "") for x in catalog.get("emotions") or [] if isinstance(x, dict) and x.get("name")
            ]
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": f"unknown {kind_q}: {name_q!r}",
                "valid_names": sorted(set(valid)),
                "t": time.time(),
            },
        )
    req_id = uuid.uuid4().hex[:16]
    pairs = design_frames_to_pb_chain(ent.get("frames") or [], runtime_req=req_id)
    if not pairs:
        return JSONResponse(status_code=500, content={"ok": False, "error": "empty frames", "t": time.time()})
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
        n = await asr_chat_hub.send_pb_chain_ordered(dev, chain, binaries_per_frame=binaries_per_frame)
    except Exception:
        logger.exception("[HTTP] /api/device_face_play 下发异常 device_id=%s kind=%s name=%s", dev, kind_q, expr_name)
        n = 0
    hint = None
    channels: dict[str, int] = {}
    if n == 0:
        channels = _registry_channels(registry, dev)
        hint = f"没有发往 WebSocket：该 device_id 当前无已连接的 /asr_chat。当前注册通道={channels or '无'}。"
    return JSONResponse(
        status_code=200,
        content={
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


@router.api_route("/api/device_tts", methods=["GET", "POST"])
@require_api_auth
async def api_device_tts(request: Request) -> JSONResponse:
    from deskbot_server.controller.runtime import get_runtime
    from deskbot_server.infrastructure.ws.downlink_adapter import WsDownlinkAdapter
    from deskbot_server.service.application.chat_flow import run_device_tts_only
    from deskbot_server.service.application.ws_chat_turn import publish_ws_chat_turn

    rt = get_runtime()
    asr_chat_hub = rt.asr_chat_hub
    chat = rt.chat
    device_pipeline_broker = rt.device_pipeline_broker
    registry = rt.registry
    qargs = _request_qargs(request)
    peer = _request_peer(request)
    method = request.method.upper()
    body = await request.body() if method in ("POST", "PUT", "PATCH") else b""

    dev = ""
    text = ""
    scene_q = ""
    if method == "POST":
        try:
            raw_body = (body or b"").decode("utf-8")
            payload = json.loads(raw_body) if raw_body.strip() else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return JSONResponse(status_code=400, content={"ok": False, "error": "invalid JSON body", "t": time.time()})
        if isinstance(payload, dict):
            dev = str(payload.get("device_id") or "").strip()
            text = str(payload.get("text") or "").strip()
            scene_q = str(payload.get("scene") or "").strip()
    else:
        dev = (qargs.get("device_id") or "").strip()
        text = (qargs.get("text") or "").strip()
        scene_q = (qargs.get("scene") or "").strip()
    if not dev:
        return JSONResponse(status_code=400, content={"ok": False, "error": "missing device_id", "t": time.time()})
    denied = device_access_denied(request.state.api_auth, dev)
    if denied is not None:
        return denied
    if not text:
        return JSONResponse(status_code=400, content={"ok": False, "error": "missing text", "t": time.time()})
    ws = await asr_chat_hub.first_ws(dev)
    if ws is None:
        return JSONResponse(
            status_code=200,
            content={
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
        downlink = WsDownlinkAdapter(ws, settings=settings, device_id=dev, dp_broker=broker)
        try:
            turn = await run_device_tts_only(
                downlink, chat, text, request_id=req_id, device_id=dev, scenes=[scene_q] if scene_q else None
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
            logger.exception("[HTTP] /api/device_tts job failed device_id=%s req=%s", dev, req_id)

    asyncio.create_task(_device_tts_job())
    logger.info(
        "[HTTP] %s /api/device_tts accepted peer=%s device_id=%s req=%s text=%r", method, peer, dev, req_id, text[:120]
    )
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "accepted": True,
            "device_id": dev,
            "text": text,
            "scene": scene_q or None,
            "req": req_id,
            "t": time.time(),
        },
    )


@router.api_route("/api/scene_playbook/run", methods=["GET", "POST"])
@require_api_auth
async def api_scene_playbook_run(request: Request) -> JSONResponse:
    from deskbot_server.controller.runtime import get_runtime
    from deskbot_server.dao.scene_playbooks_store import (
        find_playbook_by_name,
        load_scene_playbooks_file,
        normalize_playbook,
    )
    from deskbot_server.infrastructure.ws.downlink_adapter import WsDownlinkAdapter
    from deskbot_server.service.application.chat_flow import run_device_playbook

    rt = get_runtime()
    asr_chat_hub = rt.asr_chat_hub
    chat = rt.chat
    device_pipeline_broker = rt.device_pipeline_broker
    qargs = _request_qargs(request)
    peer = _request_peer(request)
    method = request.method.upper()
    body = await request.body() if method in ("POST", "PUT", "PATCH") else b""

    dev = ""
    playbook_raw: object = None
    if method == "POST":
        try:
            raw_body = (body or b"").decode("utf-8")
            payload = json.loads(raw_body) if raw_body.strip() else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return JSONResponse(status_code=400, content={"ok": False, "error": "invalid JSON body", "t": time.time()})
        if isinstance(payload, dict):
            dev = str(payload.get("device_id") or "").strip()
            if "playbook" in payload:
                playbook_raw = payload.get("playbook")
            elif payload.get("name"):
                rows = load_scene_playbooks_file(seed_if_missing=False, device_id=dev or None) or []
                playbook_raw = find_playbook_by_name(rows, str(payload.get("name")))
    else:
        dev = (qargs.get("device_id") or "").strip()
        name_q = (qargs.get("name") or "").strip()
        if name_q:
            rows = load_scene_playbooks_file(seed_if_missing=False, device_id=dev or None) or []
            playbook_raw = find_playbook_by_name(rows, name_q)
    if not dev:
        return JSONResponse(status_code=400, content={"ok": False, "error": "missing device_id", "t": time.time()})
    denied = device_access_denied(request.state.api_auth, dev)
    if denied is not None:
        return denied
    if not playbook_raw:
        return JSONResponse(
            status_code=400, content={"ok": False, "error": "missing playbook or unknown name", "t": time.time()}
        )
    try:
        playbook = normalize_playbook(playbook_raw)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc), "t": time.time()})
    ws = await asr_chat_hub.first_ws(dev)
    if ws is None:
        return JSONResponse(
            status_code=200,
            content={
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

    async def _playbook_job() -> None:
        downlink = WsDownlinkAdapter(ws, settings=settings, device_id=dev, dp_broker=broker)
        try:
            turn = await run_device_playbook(downlink, chat, playbook, request_id=req_id, device_id=dev)
            ok = (turn.status or "ok") == "ok" and not turn.error
            logger.info(
                "[HTTP] /api/scene_playbook/run done device_id=%s req=%s name=%s ok=%s err=%s",
                dev,
                req_id,
                playbook.get("name"),
                ok,
                turn.error,
            )
        except Exception:
            logger.exception("[HTTP] /api/scene_playbook/run failed device_id=%s req=%s", dev, req_id)

    asyncio.create_task(_playbook_job())
    logger.info(
        "[HTTP] %s /api/scene_playbook/run accepted peer=%s device_id=%s req=%s name=%s",
        method,
        peer,
        dev,
        req_id,
        playbook.get("name"),
    )
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "accepted": True,
            "device_id": dev,
            "name": playbook.get("name"),
            "req": req_id,
            "interleaved": True,
            "t": time.time(),
        },
    )


@router.get("/api/device_pb_scene")
@require_api_auth
async def api_device_pb_scene(request: Request) -> JSONResponse:
    from deskbot_server.controller.runtime import get_runtime

    asr_chat_hub = get_runtime().asr_chat_hub
    qargs = _request_qargs(request)
    peer = _request_peer(request)
    dev = (qargs.get("device_id") or "").strip()
    scene_q = (qargs.get("scene") or "").strip()
    if not dev:
        return JSONResponse(status_code=400, content={"error": "missing device_id", "t": time.time()})
    denied = device_access_denied(request.state.api_auth, dev)
    if denied is not None:
        return denied
    if not scene_q:
        return JSONResponse(status_code=400, content={"error": "missing scene", "t": time.time()})
    req_id = uuid.uuid4().hex[:16]
    ent = _pb_scene_entry_by_name({}, scene_q, device_id=dev)
    if ent is None:
        valid = _pb_scene_keys_sorted(None)
        return JSONResponse(
            status_code=400,
            content={"error": f"unknown or empty scene: {scene_q!r}", "valid_scenes": valid, "t": time.time()},
        )
    pairs = design_frames_to_pb_chain(ent.get("frames") or [], runtime_req=req_id)
    if not pairs:
        return JSONResponse(status_code=500, content={"error": "empty frames", "t": time.time()})
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
        n = await asr_chat_hub.send_pb_chain_ordered(dev, frames, binaries_per_frame=binaries_per_frame)
    except Exception:
        logger.exception("[HTTP] /api/device_pb_scene 下发异常 device_id=%s scene=%s", dev, scene_log)
        return JSONResponse(
            status_code=500,
            content={
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
    return JSONResponse(
        status_code=200,
        content={
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


@router.api_route("/api/servo_config", methods=["GET", "POST"])
@require_api_auth
async def api_servo_config(request: Request) -> JSONResponse:
    qargs = _request_qargs(request)
    peer = _request_peer(request)
    method = request.method.upper()
    cfg_dev = _config_device_id(qargs)
    cfg_path = resolve_json_path(SERVO_CFG_FILE, cfg_dev)
    if method == "GET":
        try:
            cfg = await run_blocking(load_servo_cfg_file, device_id=cfg_dev)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("[HTTP] GET /api/servo_config read failed peer=%s err=%s", peer, exc)
            return JSONResponse(status_code=500, content={"ok": False, "error": str(exc), "t": time.time()})
        if cfg is None:
            return JSONResponse(
                status_code=200,
                content={
                    "ok": True,
                    "exists": False,
                    "file": os.path.basename(cfg_path),
                    "device_id": cfg_dev,
                    "t": time.time(),
                },
            )
        logger.info(
            "[HTTP] GET /api/servo_config peer=%s device_id=%s -> %s", peer, cfg_dev, os.path.basename(cfg_path)
        )
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "exists": True,
                "config": cfg,
                "file": os.path.basename(cfg_path),
                "device_id": cfg_dev,
                "t": time.time(),
            },
        )
    if method == "POST":
        body = await request.body()
        try:
            raw_body = (body or b"").decode("utf-8")
            payload = json.loads(raw_body) if raw_body.strip() else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return JSONResponse(status_code=400, content={"ok": False, "error": "invalid JSON body", "t": time.time()})
        cfg_dev = _config_device_id(qargs, payload if isinstance(payload, dict) else None)
        cfg_path = resolve_json_path(SERVO_CFG_FILE, cfg_dev)
        try:
            cfg = normalize_servo_document(payload, require_presets=True)
            await run_blocking(save_servo_cfg_file, cfg, device_id=cfg_dev)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"ok": False, "error": str(exc), "t": time.time()})
        except OSError as exc:
            logger.warning("[HTTP] POST /api/servo_config write failed peer=%s err=%s", peer, exc)
            return JSONResponse(status_code=500, content={"ok": False, "error": str(exc), "t": time.time()})
        logger.info("[HTTP] POST /api/servo_config peer=%s device_id=%s -> %s", peer, cfg_dev, cfg_path)
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "config": cfg,
                "file": os.path.basename(cfg_path),
                "device_id": cfg_dev,
                "t": time.time(),
            },
        )
    return JSONResponse(status_code=405, content={"ok": False, "error": "method not allowed", "t": time.time()})


@router.api_route("/api/device_pb_anim", methods=["GET", "POST"])
@require_api_auth
async def api_device_pb_anim(request: Request) -> JSONResponse:
    from deskbot_server.controller.runtime import get_runtime

    rt = get_runtime()
    asr_chat_hub = rt.asr_chat_hub
    registry = rt.registry
    qargs = _request_qargs(request)
    peer = _request_peer(request)
    method = request.method.upper()

    anim: Optional[dict[str, Any] | list] = None
    dev = ""
    chunk_ms = 500
    act = PB_ACTION_REPLACE
    pb_level = PB_LEVEL_DEBUG

    if method == "GET":
        dev = (qargs.get("device_id") or "").strip()
        anim_b64 = (qargs.get("anim_b64") or "").strip()
        if not dev:
            return JSONResponse(status_code=400, content={"ok": False, "error": "missing device_id", "t": time.time()})
        if not anim_b64:
            return JSONResponse(status_code=400, content={"ok": False, "error": "missing anim_b64", "t": time.time()})
        try:
            pad = (-len(anim_b64)) % 4
            anim = json.loads(base64.b64decode(anim_b64 + ("=" * pad)).decode("utf-8"))
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            return JSONResponse(
                status_code=400, content={"ok": False, "error": f"invalid anim_b64: {exc}", "t": time.time()}
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
        body_bytes = await request.body()
        try:
            raw_body = (body_bytes or b"").decode("utf-8")
            body = json.loads(raw_body) if raw_body.strip() else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return JSONResponse(status_code=400, content={"ok": False, "error": "invalid JSON body", "t": time.time()})
        if not isinstance(body, dict):
            body = {}
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
        return JSONResponse(status_code=405, content={"ok": False, "error": "method not allowed", "t": time.time()})

    if not dev:
        return JSONResponse(status_code=400, content={"ok": False, "error": "missing device_id", "t": time.time()})
    denied = device_access_denied(request.state.api_auth, dev)
    if denied is not None:
        return denied
    if not isinstance(anim, (dict, list)):
        return JSONResponse(
            status_code=400, content={"ok": False, "error": "anim must be array or object", "t": time.time()}
        )
    if isinstance(anim, dict):
        if not isinstance(anim.get("elements"), dict):
            return JSONResponse(
                status_code=400, content={"ok": False, "error": "anim.elements required", "t": time.time()}
            )
        anim_list = [{"elements": copy.deepcopy(anim["elements"]), "ms": chunk_ms}]
    else:
        anim_list = []
        for one in anim:
            if not isinstance(one, dict) or not isinstance(one.get("elements"), dict):
                return JSONResponse(
                    status_code=400, content={"ok": False, "error": "anim[] items need elements", "t": time.time()}
                )
            try:
                item_ms = int(one.get("ms") or chunk_ms)
            except (TypeError, ValueError):
                item_ms = chunk_ms
            item: dict[str, Any] = {"elements": copy.deepcopy(one["elements"]), "ms": max(1, item_ms)}
            ph = str(one.get("phoneme") or "").strip()
            if ph:
                item["phoneme"] = ph
            anim_list.append(item)
        if not anim_list:
            return JSONResponse(
                status_code=400, content={"ok": False, "error": "anim[] must not be empty", "t": time.time()}
            )
        chunk_ms = sum(int(x["ms"]) for x in anim_list)
    chunk_ms = max(50, min(10000, chunk_ms))
    if act not in (PB_ACTION_REPLACE, PB_ACTION_APPEND, PB_ACTION_DEFAULT):
        return JSONResponse(status_code=400, content={"ok": False, "error": "invalid action", "t": time.time()})
    if pb_level not in (0, 1, 2, 3):
        return JSONResponse(status_code=400, content={"ok": False, "error": "invalid level", "t": time.time()})
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
        logger.exception("[HTTP] /api/device_pb_anim 下发异常 device_id=%s", dev)
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
            "[HTTP] %s /api/device_pb_anim delivered=0 device_id=%s registry_channels=%s", method, dev, channels or None
        )
    logger.info("[HTTP] %s /api/device_pb_anim peer=%s device_id=%s req=%s delivered=%d", method, peer, dev, req_id, n)
    return JSONResponse(
        status_code=200,
        content={
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


@router.get("/api/device_pb_expr_scene")
@require_api_auth
async def api_device_pb_expr_scene(request: Request) -> JSONResponse:
    if request.method.upper() != "GET":
        return JSONResponse(status_code=405, content={"ok": False, "error": "method not allowed", "t": time.time()})
    from deskbot_server.controller.runtime import get_runtime

    rt = get_runtime()
    asr_chat_hub = rt.asr_chat_hub
    registry = rt.registry
    qargs = _request_qargs(request)
    dev = (qargs.get("device_id") or "").strip()
    scene_q = (qargs.get("scene") or qargs.get("name") or "").strip()
    if not dev:
        return JSONResponse(status_code=400, content={"ok": False, "error": "missing device_id", "t": time.time()})
    denied = device_access_denied(request.state.api_auth, dev)
    if denied is not None:
        return denied
    if not scene_q:
        return JSONResponse(status_code=400, content={"ok": False, "error": "missing scene", "t": time.time()})
    try:
        rows = load_face_expr_scenes_file(seed_if_missing=False, device_id=dev) or []
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc), "t": time.time()})
    ent = find_design_scene_by_name(rows, scene_q)
    if ent is None:
        valid = sorted({str(r.get("name") or "") for r in rows if r.get("name")})
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": f"unknown scene: {scene_q!r}", "valid_scenes": valid, "t": time.time()},
        )
    req_id = uuid.uuid4().hex[:16]
    pairs = design_frames_to_pb_chain(ent.get("frames") or [], runtime_req=req_id)
    if not pairs:
        return JSONResponse(status_code=500, content={"ok": False, "error": "empty frames", "t": time.time()})
    chain = [msg for msg, _bins in pairs]
    binaries_per_frame = [list(_bins) for _msg, _bins in pairs]
    try:
        n = await asr_chat_hub.send_pb_chain_ordered(dev, chain, binaries_per_frame=binaries_per_frame)
    except Exception:
        logger.exception("[HTTP] /api/device_pb_expr_scene 下发异常 device_id=%s scene=%s", dev, scene_q)
        n = 0
    hint = None
    channels: dict[str, int] = {}
    if n == 0:
        channels = _registry_channels(registry, dev)
        hint = f"没有发往 WebSocket：该 device_id 当前无已连接的 /asr_chat。当前注册通道={channels or '无'}。"
    return JSONResponse(
        status_code=200,
        content={
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


# ---- WebSocket：调试订阅（鉴权与设备侧不同）----


@router.websocket("/camera_view")
@require_web_ws_subscriber_auth
async def camera_view(websocket: WebSocket) -> None:
    st = websocket.state
    ws = st.ws
    url_device = st.device_id
    face_svc = CameraFaceService()
    conn_id = uuid.uuid4().hex
    peer = WsUtils.peer_str(ws)
    send_task: asyncio.Task | None = None
    logger.info("[/camera_view] 订阅者接入 peer=%s device_filter=%s conn_id=%s", peer, url_device, conn_id)

    await WsUtils.safe_send(
        ws,
        _json_msg(
            {
                "type": "ready",
                "channel": "camera_view",
                "device_filter": url_device,
                "expects": "binary JPEG frames preceded by camera_frame meta",
            }
        ),
    )

    async def on_video_frame(device_id: str, frame: bytes, meta: dict[str, Any]) -> None:
        nonlocal send_task
        # 上一帧尚未发完则直接丢弃
        if send_task is not None and not send_task.done():
            logger.info(
                "[/camera_view] 发送偏慢，丢弃一帧 peer=%s device_id=%s conn_id=%s bytes=%d",
                peer,
                device_id,
                conn_id,
                len(frame),
            )
            return

        meta_json = json.dumps(meta, ensure_ascii=False)

        async def _send_pair() -> None:
            await WsUtils.safe_send(ws, meta_json)
            await WsUtils.safe_send(ws, frame)

        send_task = asyncio.create_task(_send_pair())

    await face_svc.subscribe_video_stream(conn_id, on_video_frame, device_id=url_device)
    try:
        # 客户端不发业务消息；只读到断开以维持连接
        async for _msg in ws:
            pass
    except ConnectionClosed as closed:
        logger.info(
            "/camera_view WebSocket 已关闭 peer=%s device_filter=%s conn_id=%s: %s", peer, url_device, conn_id, closed
        )
    finally:
        await face_svc.unsubscribe_video_stream(conn_id)
        if send_task is not None and not send_task.done():
            send_task.cancel()


@router.websocket("/device_pipeline")
@require_web_ws_pipeline_auth
async def device_pipeline(websocket: WebSocket) -> None:
    rt = get_runtime()
    st = websocket.state
    await handle_device_pipeline(
        st.ws, rt.device_pipeline_broker, rt.registry, pin_code=getattr(st, "pin_code", None)
    )
