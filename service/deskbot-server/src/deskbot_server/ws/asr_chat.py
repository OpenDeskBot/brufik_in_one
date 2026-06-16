from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Optional

from websockets.exceptions import ConnectionClosed

from deskbot_server.application.asr_chat_uplink import (
    AsrChatCameraPipeline,
    PendingUplinkBinary,
    coerce_next_bin_len,
)
from deskbot_server.application.camera_broker import CameraImageBroker
from deskbot_server.pipeline.audio import AudioConfig, ConnectionSession
from deskbot_server.ws.chat_turn import publish_ws_chat_turn, run_ws_chat_turn
from deskbot_server.application.chat_service import ChatService
from deskbot_server.infrastructure.ws.downlink_adapter import WsDownlinkAdapter
from deskbot_server.util import (
    _format_ts,
    _json_msg,
    _ms_between,
    _new_request_id,
    _normalize_incoming_pb_ack,
    _peer_str,
    format_exc_detail,
)
from deskbot_server.vision.undistort import CameraFaceRuntime
from deskbot_server.ws.asr_chat_hub import AsrChatHub
from deskbot_server.ws.device_pipeline import DevicePipelineBroker
from deskbot_server.ws.registry import DeviceRegistry
from deskbot_server.ws.api_key_gate import record_turn_usage
from deskbot_server.ws.ws_send import _safe_send

logger = logging.getLogger("deskbot-server")


async def _schedule_asr_turn(
    websocket,
    *,
    pipeline: ChatService,
    audio_cfg: AudioConfig,
    session: ConnectionSession,
    pcm_segment: bytes,
    device_id: Optional[str],
    dp_broker: DevicePipelineBroker,
    registry: DeviceRegistry,
    turn_task_holder: list,
    api_key_id: Optional[str] = None,
) -> None:
    """``device_pb_only`` 下后台跑一轮，避免阻塞 WS 读循环（否则收不到 ``pb_ack``）。"""
    prev = turn_task_holder[0] if turn_task_holder else None
    if prev is not None and not prev.done():
        logger.info(
            "[/asr_chat] 上一轮未完成，跳过本次触发 device_id=%s",
            device_id,
        )
        return

    async def _job() -> None:
        try:
            await _run_asr_turn(
                websocket,
                pipeline=pipeline,
                audio_cfg=audio_cfg,
                session=session,
                pcm_segment=pcm_segment,
                device_id=device_id,
                dp_broker=dp_broker,
                registry=registry,
                api_key_id=api_key_id,
            )
        except Exception:
            logger.exception(
                "[/asr_chat] 后台 ASR 轮次异常 device_id=%s",
                device_id,
            )

    task = asyncio.create_task(_job())
    turn_task_holder.clear()
    turn_task_holder.append(task)


async def _publish_asr_terminal(
    dp_broker: DevicePipelineBroker,
    registry: DeviceRegistry,
    device_id: Optional[str],
    *,
    request_id: str,
    asr_text: Optional[str],
    asr_ms: Optional[float],
    t_asr_start: float,
    t_asr_text: float,
    status: str,
    error: str,
) -> None:
    """ASR 未进入 LLM 时仍写入流水（空识别、过滤等）。"""
    if not device_id or dp_broker is None:
        return
    await publish_ws_chat_turn(
        dp_broker,
        registry,
        device_id,
        source="asr",
        asr_text=asr_text,
        t_asr_start=t_asr_start,
        t_asr_text=t_asr_text,
        flow={
            "status": status,
            "error": error,
            "t_llm_end": t_asr_text,
            "t_tts_end": t_asr_text,
        },
        request_id=request_id,
    )


async def _run_asr_turn(
    websocket,
    *,
    pipeline: ChatService,
    audio_cfg: AudioConfig,
    session: ConnectionSession,
    pcm_segment: bytes,
    device_id: Optional[str],
    dp_broker: DevicePipelineBroker,
    registry: DeviceRegistry,
    api_key_id: Optional[str] = None,
) -> None:
    request_id = _new_request_id()
    seg_duration_ms = int(len(pcm_segment) / 2 / audio_cfg.sample_rate * 1000)
    t_asr_start = time.monotonic()
    text = await pipeline.asr(pcm_segment, sample_rate=audio_cfg.sample_rate)
    if api_key_id:
        record_turn_usage(api_key_id, device_id=device_id, asr_bytes=len(pcm_segment))
    t_asr_text = time.monotonic()
    asr_ms = _ms_between(t_asr_start, t_asr_text)
    if not text:
        logger.info(
            "[ASR] 结果为空 device_id=%s req=%s audio_ms=%d asr_ms=%s",
            device_id,
            request_id,
            seg_duration_ms,
            asr_ms,
        )
        await _publish_asr_terminal(
            dp_broker,
            registry,
            device_id,
            request_id=request_id,
            asr_text=None,
            asr_ms=asr_ms,
            t_asr_start=t_asr_start,
            t_asr_text=t_asr_text,
            status="error",
            error="asr_empty",
        )
        return
    if not pipeline.is_valid_asr_text(text):
        logger.info(
            "[ASR] 结果被过滤 device_id=%s req=%s audio_ms=%d asr_ms=%s text=%r",
            device_id,
            request_id,
            seg_duration_ms,
            asr_ms,
            text,
        )
        await _publish_asr_terminal(
            dp_broker,
            registry,
            device_id,
            request_id=request_id,
            asr_text=text,
            asr_ms=asr_ms,
            t_asr_start=t_asr_start,
            t_asr_text=t_asr_text,
            status="error",
            error="asr_filtered",
        )
        return
    logger.info(
        "[ASR] 识别成功 device_id=%s req=%s audio_ms=%d asr_ms=%s text=%r",
        device_id,
        request_id,
        seg_duration_ms,
        asr_ms,
        text,
    )
    downlink = WsDownlinkAdapter(
        websocket,
        settings=pipeline.settings,
        device_id=device_id,
        dp_broker=dp_broker,
    )
    await downlink.emit_stage(
        "asr_done",
        request_id=request_id,
        send_client=False,
        event_fields={
            "asr_text": text,
            "asr_ms": asr_ms,
            "source": "asr",
        },
    )
    try:
        flow = await run_ws_chat_turn(
            websocket,
            pipeline,
            text,
            request_id=request_id,
            dp_broker=dp_broker,
            registry=registry,
            device_id=device_id,
            t_asr_start=t_asr_start,
            t_asr_text=t_asr_text,
        )
    except Exception as exc:
        logger.exception(
            "[ASR] 对话轮次异常 device_id=%s req=%s",
            device_id,
            request_id,
        )
        await publish_ws_chat_turn(
            dp_broker,
            registry,
            device_id,
            source="asr",
            asr_text=text,
            t_asr_start=t_asr_start,
            t_asr_text=t_asr_text,
            flow={
                "status": "error",
                "error": str(exc),
                "t_llm_end": t_asr_text,
                "t_tts_end": t_asr_text,
            },
            request_id=request_id,
        )
        return
    await publish_ws_chat_turn(
        dp_broker,
        registry,
        device_id,
        source="asr",
        asr_text=text,
        t_asr_start=t_asr_start,
        t_asr_text=t_asr_text,
        flow=flow,
        request_id=request_id,
    )
    if api_key_id:
        llm_out = (flow.get("llm_raw") or flow.get("llm_text") or "")
        llm_bytes = len(text.encode("utf-8")) + len(str(llm_out).encode("utf-8"))
        tts_bytes = len(str(flow.get("llm_text") or "").encode("utf-8")) * 48
        record_turn_usage(api_key_id, device_id=device_id, llm_bytes=llm_bytes, tts_bytes=tts_bytes)


async def handle_asr_chat(
    websocket,
    pipeline: ChatService,
    audio_cfg: AudioConfig,
    device_id: Optional[str],
    registry: DeviceRegistry,
    dp_broker: DevicePipelineBroker,
    asr_chat_hub: AsrChatHub,
    camera_image_broker: Optional[CameraImageBroker] = None,
    camera_face_runtime: Optional[CameraFaceRuntime] = None,
    *,
    send_face_info_to_asr_chat: bool = False,
    api_key_id: Optional[str] = None,
) -> None:
    """/asr_chat WS：音频/文本上行；可选 ``camera_frame`` + JPEG（``next_bin_len``）。"""
    session = ConnectionSession(pipeline, audio_cfg)
    peer = _peer_str(websocket)
    pending: Optional[PendingUplinkBinary] = None
    turn_task_holder: list[asyncio.Task] = []
    device_pb_only = getattr(pipeline, "asr_chat_device_pb_only", False)
    camera_pipe: Optional[AsrChatCameraPipeline] = None
    if device_id and camera_face_runtime is not None:
        from deskbot_server.config import load_config
        from deskbot_server.vision.undistort import build_camera_face_runtime

        device_runtime = build_camera_face_runtime(load_config(), device_id=device_id)
        camera_pipe = AsrChatCameraPipeline(
            runtime=device_runtime,
            device_id=device_id,
        )

    if device_id:
        await registry.connect(device_id, "asr_chat", websocket)
        await asr_chat_hub.attach(device_id, websocket)
        logger.info(
            "[/asr_chat] 接入 device_id=%s peer=%s (已登记到 DeviceRegistry)",
            device_id,
            peer,
        )
    else:
        logger.warning(
            "[/asr_chat] 接入缺失 device_id peer=%s —— 不会出现在 /api/devices 设备列表，"
            "请改用 ws://host:9000/asr_chat?device_id=<设备ID>",
            peer,
        )
    try:
        await _safe_send(
            websocket, _json_msg({"type": "ready", "device_id": device_id})
        )

        async for message in websocket:
            try:
                # --- 等待中的 binary（上一帧 JSON 已声明 next_bin_len）---
                if pending is not None:
                    if not isinstance(message, (bytes, bytearray)):
                        logger.warning(
                            "[/asr_chat] device_id=%s 预期 %d 字节 binary，收到 JSON，丢弃",
                            device_id,
                            pending.length,
                        )
                        pending = None
                        continue
                    payload = bytes(message)
                    if len(payload) != pending.length:
                        logger.warning(
                            "[/asr_chat] device_id=%s binary 长度不符 "
                            "expected=%d got=%d kind=%s",
                            device_id,
                            pending.length,
                            len(payload),
                            pending.kind,
                        )
                        pending = None
                        continue
                    kind = pending.kind
                    codec = pending.codec
                    pending = None

                    if kind == "camera_frame":
                        if camera_pipe is None or camera_image_broker is None:
                            logger.warning(
                                "[/asr_chat] 收到 camera_frame 但未配置 camera 运行时"
                            )
                            continue
                        if api_key_id:
                            record_turn_usage(api_key_id, device_id=device_id, face_bytes=len(payload))
                        await camera_pipe.process_jpeg(
                            payload,
                            image_broker=camera_image_broker,
                            dp_broker=dp_broker,
                            asr_chat_hub=asr_chat_hub,
                            send_face_info_to_asr_chat=send_face_info_to_asr_chat,
                        )
                        continue

                    pcm_segment = await session.feed_audio(payload, codec)
                    if pcm_segment is None:
                        continue
                    if device_pb_only:
                        await _schedule_asr_turn(
                            websocket,
                            pipeline=pipeline,
                            audio_cfg=audio_cfg,
                            session=session,
                            pcm_segment=pcm_segment,
                            device_id=device_id,
                            dp_broker=dp_broker,
                            registry=registry,
                            turn_task_holder=turn_task_holder,
                            api_key_id=api_key_id,
                        )
                    else:
                        await _run_asr_turn(
                            websocket,
                            pipeline=pipeline,
                            audio_cfg=audio_cfg,
                            session=session,
                            pcm_segment=pcm_segment,
                            device_id=device_id,
                            dp_broker=dp_broker,
                            registry=registry,
                            api_key_id=api_key_id,
                        )
                    continue

                # --- 裸 binary：兼容旧固件（仅音频）---
                if isinstance(message, (bytes, bytearray)):
                    payload = bytes(message)
                    pcm_segment = await session.feed_audio(payload, None)
                    if pcm_segment is None:
                        continue
                    if device_pb_only:
                        await _schedule_asr_turn(
                            websocket,
                            pipeline=pipeline,
                            audio_cfg=audio_cfg,
                            session=session,
                            pcm_segment=pcm_segment,
                            device_id=device_id,
                            dp_broker=dp_broker,
                            registry=registry,
                            turn_task_holder=turn_task_holder,
                            api_key_id=api_key_id,
                        )
                    else:
                        await _run_asr_turn(
                            websocket,
                            pipeline=pipeline,
                            audio_cfg=audio_cfg,
                            session=session,
                            pcm_segment=pcm_segment,
                            device_id=device_id,
                            dp_broker=dp_broker,
                            registry=registry,
                            api_key_id=api_key_id,
                        )
                    continue

                data = json.loads(message)
                msg_type = data.get("type")

                if msg_type == "ping":
                    if not getattr(pipeline, "asr_chat_device_pb_only", False):
                        await _safe_send(websocket, _json_msg({"type": "pong"}))
                    continue

                if msg_type == "pb_ack":
                    norm = _normalize_incoming_pb_ack(data)
                    if norm is not None and device_id:
                        await registry.record_pb_ack(device_id, norm)
                        logger.info(
                            "[pb_ack] device_id=%s req=%r idx=%s audio_buf_ms=%s servo=%s",
                            device_id,
                            norm.get("req"),
                            norm.get("idx"),
                            norm.get("audio_buf_ms"),
                            norm.get("servo"),
                        )
                        if dp_broker is not None:
                            now_ts = time.time()
                            await dp_broker.broadcast_to_device(
                                device_id,
                                {
                                    "type": "pipeline_stage",
                                    "event": {
                                        "device_id": device_id,
                                        "request_id": None,
                                        "stage": "pb_ack",
                                        "ack": norm,
                                        "ts": now_ts,
                                        "t_mono": time.monotonic(),
                                        "received_at": _format_ts(now_ts),
                                    },
                                },
                            )
                    elif norm is not None and not device_id:
                        logger.info(
                            "[pb_ack] 已解析但连接无 device_id，未入库 peer=%s",
                            peer,
                        )
                    continue

                if msg_type == "user_text":
                    ut = (data.get("text") or "").strip()
                    if not ut or not pipeline.is_valid_asr_text(ut):
                        continue
                    request_id = _new_request_id()
                    t_asr_start = time.monotonic()
                    t_asr_text = time.monotonic()
                    text_downlink = WsDownlinkAdapter(
                        websocket,
                        settings=pipeline.settings,
                        device_id=device_id,
                        dp_broker=dp_broker,
                    )
                    await text_downlink.emit_stage(
                        "asr_done",
                        request_id=request_id,
                        send_client=False,
                        event_fields={
                            "asr_text": ut,
                            "asr_ms": 0,
                            "source": "text",
                        },
                    )
                    flow = await run_ws_chat_turn(
                        websocket,
                        pipeline,
                        ut,
                        request_id=request_id,
                        dp_broker=dp_broker,
                        registry=registry,
                        device_id=device_id,
                        t_asr_start=t_asr_start,
                        t_asr_text=t_asr_text,
                    )
                    await publish_ws_chat_turn(
                        dp_broker,
                        registry,
                        device_id,
                        source="text",
                        asr_text=ut,
                        t_asr_start=t_asr_start,
                        t_asr_text=t_asr_text,
                        flow=flow,
                        request_id=request_id,
                    )
                    continue

                if msg_type == "flush":
                    pcm_segment = session.flush()
                    if pcm_segment is None:
                        continue
                    if device_pb_only:
                        await _schedule_asr_turn(
                            websocket,
                            pipeline=pipeline,
                            audio_cfg=audio_cfg,
                            session=session,
                            pcm_segment=pcm_segment,
                            device_id=device_id,
                            dp_broker=dp_broker,
                            registry=registry,
                            turn_task_holder=turn_task_holder,
                            api_key_id=api_key_id,
                        )
                    else:
                        await _run_asr_turn(
                            websocket,
                            pipeline=pipeline,
                            audio_cfg=audio_cfg,
                            session=session,
                            pcm_segment=pcm_segment,
                            device_id=device_id,
                            dp_broker=dp_broker,
                            registry=registry,
                            api_key_id=api_key_id,
                        )
                    continue

                if msg_type == "camera_frame":
                    nbl = coerce_next_bin_len(data)
                    if nbl > 0:
                        pending = PendingUplinkBinary(
                            kind="camera_frame",
                            length=nbl,
                        )
                        continue
                    logger.warning(
                        "[/asr_chat] camera_frame 缺少 next_bin_len device_id=%s",
                        device_id,
                    )
                    continue

                if msg_type == "audio":
                    nbl = coerce_next_bin_len(data)
                    if nbl > 0:
                        pending = PendingUplinkBinary(
                            kind="audio",
                            length=nbl,
                            codec=data.get("codec"),
                        )
                        continue
                    raw_b64 = data.get("data")
                    if raw_b64:
                        payload = base64.b64decode(raw_b64)
                        codec = data.get("codec")
                        pcm_segment = await session.feed_audio(payload, codec)
                        if pcm_segment is None:
                            continue
                        if device_pb_only:
                            await _schedule_asr_turn(
                                websocket,
                                pipeline=pipeline,
                                audio_cfg=audio_cfg,
                                session=session,
                                pcm_segment=pcm_segment,
                                device_id=device_id,
                                dp_broker=dp_broker,
                                registry=registry,
                                turn_task_holder=turn_task_holder,
                                api_key_id=api_key_id,
                            )
                        else:
                            await _run_asr_turn(
                                websocket,
                                pipeline=pipeline,
                                audio_cfg=audio_cfg,
                                session=session,
                                pcm_segment=pcm_segment,
                                device_id=device_id,
                                dp_broker=dp_broker,
                                registry=registry,
                                api_key_id=api_key_id,
                            )
                    continue

            except Exception as exc:
                logger.exception("处理客户端消息失败: %s", format_exc_detail(exc))
    except ConnectionClosed as closed:
        logger.info("WebSocket 已关闭: %s", closed)
    finally:
        if device_id:
            await asr_chat_hub.detach(device_id, websocket)
            await registry.disconnect(websocket)
