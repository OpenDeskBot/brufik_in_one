from __future__ import annotations

import asyncio
import base64
import json
import logging
import time

from websockets.exceptions import ConnectionClosed

from deskbot_server.infrastructure.ws.downlink_adapter import WsDownlinkAdapter
from deskbot_server.service.application.asr_chat_uplink import (
    PendingUplinkBinary,
    coerce_audio_flush,
    coerce_next_bin_len,
    coerce_opus_frames,
    pack_ws_downlink_frame,
    parse_packed_frame,
)
from deskbot_server.service.application.boot_wake import deliver_boot_wake_scene
from deskbot_server.service.application.chat_service import ChatService
from deskbot_server.service.application.interaction_feedback import (
    schedule_listen_feedback,
    start_llm_wait_nod_feedback,
    stop_llm_wait_nod_feedback,
)
from deskbot_server.service.application.ws_chat_turn import publish_ws_chat_turn, run_ws_chat_turn
from deskbot_server.service.asr_service import AsrService
from deskbot_server.service.camera_face_service import CameraFaceService
from deskbot_server.service.live_service import LiveService
from deskbot_server.service.pipeline.audio import AudioConfig, ConnectionSession
from deskbot_server.service.vad_service import VadService
from deskbot_server.utils.async_helpers import spawn
from deskbot_server.utils.util import (
    _format_ts,
    _json_msg,
    _ms_between,
    _new_request_id,
    _normalize_incoming_pb_ack,
    format_exc_detail,
    pcm_to_wav_bytes,
)
from deskbot_server.utils.ws_utils import WsUtils
from deskbot_server.ws.asr_chat_hub import AsrChatHub
from deskbot_server.ws.device_pipeline import DevicePipelineBroker
from deskbot_server.ws.registry import DeviceRegistry
from deskbot_server.ws.uplink_rate_stats import (
    ensure_uplink_rate_stats_started,
    note_uplink_ack,
    note_uplink_audio,
    note_uplink_camera,
    remove_device,
)

logger = logging.getLogger("deskbot-server")


async def _feed_rom_uplink(
    payload: bytes,
    codec: str | None,
    *,
    session: ConnectionSession,
    asr_chat_hub: AsrChatHub,
    device_id: str | None,
    sample_rate: int | None = None,
    channels: int | None = None,
    opus_frames: int | None = None,
    websocket=None,
    pipeline: ChatService | None = None,
    audio_cfg: AudioConfig | None = None,
    dp_broker: DevicePipelineBroker | None = None,
    registry: DeviceRegistry | None = None,
    turn_task_holder: list | None = None,
    device_pb_only: bool = False,
) -> None:
    note_uplink_audio(device_id)
    utterance, uplink_started, _ = await session.feed_audio(
        payload, codec, sample_rate=sample_rate, channels=channels, opus_frames=opus_frames
    )
    if uplink_started:
        logger.info(
            "[/asr_chat] 首包 audio device_id=%s payload_bytes=%d codec=%s sr=%s ch=%s",
            device_id,
            len(payload),
            codec,
            sample_rate,
            channels,
        )
        schedule_listen_feedback(asr_chat_hub, device_id)
    if utterance and websocket is not None and pipeline is not None and audio_cfg is not None:
        await _schedule_asr_turn(
            websocket,
            pipeline=pipeline,
            audio_cfg=audio_cfg,
            session=session,
            pcm_segment=utterance,
            device_id=device_id,
            dp_broker=dp_broker,
            registry=registry,
            asr_chat_hub=asr_chat_hub,
            turn_task_holder=turn_task_holder or [],
            uplink_sample_rate=session.rom_sr,
            uplink_channels=session.rom_ch,
            uplink_codec=session.rom_codec,
        )


async def _schedule_asr_turn(
    websocket,
    *,
    pipeline: ChatService,
    audio_cfg: AudioConfig,
    session: ConnectionSession,
    pcm_segment: bytes,
    device_id: str | None,
    dp_broker: DevicePipelineBroker,
    registry: DeviceRegistry,
    asr_chat_hub: AsrChatHub,
    turn_task_holder: list,
    uplink_sample_rate: int | None = None,
    uplink_channels: int = 1,
    uplink_codec: str = "pcm16",
) -> None:
    """``device_pb_only`` 下后台跑一轮，避免阻塞 WS 读循环（否则收不到 ``pb_ack``）。"""
    prev = turn_task_holder[0] if turn_task_holder else None
    if prev is not None and not prev.done():
        logger.info("[/asr_chat] 上一轮未完成，跳过本次触发 device_id=%s", device_id)
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
                asr_chat_hub=asr_chat_hub,
                uplink_sample_rate=uplink_sample_rate,
                uplink_channels=uplink_channels,
                uplink_codec=uplink_codec,
            )
        except Exception:
            logger.exception("[/asr_chat] 后台 ASR 轮次异常 device_id=%s", device_id)

    task = asyncio.create_task(_job())
    turn_task_holder.clear()
    turn_task_holder.append(task)


async def _ingest_asr_chat_camera_frame(
    *, payload: bytes, device_id: str | None, camera_face_enabled: bool, enc: str = "binary"
) -> None:
    """读循环外异步处理：交给 CameraFaceService，不阻塞 WS 继续收帧。"""
    note_uplink_camera(device_id)
    nbytes = len(payload or b"")
    if not device_id or not camera_face_enabled:
        logger.info(
            "[camera] device_id=%s bytes=%d accepted=false reason=not_configured channel=/asr_chat enc=%s",
            device_id or "-",
            nbytes,
            enc,
        )
        return
    # 接收结果与识别耗时由 CameraFaceService.process 统一打印
    await CameraFaceService().process(device_id, payload, frame_source="asr_chat", log_channel="/asr_chat")


async def _publish_asr_capture(
    dp_broker: DevicePipelineBroker | None,
    device_id: str | None,
    *,
    request_id: str,
    pcm_segment: bytes,
    sample_rate: int,
    asr_text: str | None,
    asr_ms: float | None,
    asr_valid: bool,
    error: str | None = None,
    channels: int = 1,
    codec: str = "pcm16",
) -> None:
    """向 device_pipeline 订阅者推送 ASR 收音调试事件（仅调试台订阅时）。"""
    if not device_id or dp_broker is None or not pcm_segment:
        return
    if not await dp_broker.has_subscribers_for_device(device_id):
        return
    pcm_bytes = len(pcm_segment)
    audio_ms = int(pcm_bytes / 2 / max(1, sample_rate) * 1000)
    wav_b64 = base64.b64encode(pcm_to_wav_bytes(pcm_segment, sample_rate)).decode("ascii")
    now_ts = time.time()
    await dp_broker.broadcast_to_device(
        device_id,
        {
            "type": "asr_capture",
            "event": {
                "device_id": device_id,
                "request_id": request_id,
                "received_ts": now_ts,
                "received_at": _format_ts(now_ts),
                "asr_text": asr_text,
                "asr_valid": asr_valid,
                "asr_ms": asr_ms,
                "audio_ms": audio_ms,
                "pcm_bytes": pcm_bytes,
                "sample_rate": sample_rate,
                "channels": channels,
                "codec": codec,
                "error": error,
                "wav_base64": wav_b64,
            },
        },
    )


async def _publish_asr_terminal(
    dp_broker: DevicePipelineBroker,
    registry: DeviceRegistry,
    device_id: str | None,
    *,
    request_id: str,
    asr_text: str | None,
    asr_ms: float | None,
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
        flow={"status": status, "error": error, "t_llm_end": t_asr_text, "t_tts_end": t_asr_text},
        request_id=request_id,
    )


async def _send_mic_open_signal(asr_chat_hub: AsrChatHub | None, device_id: str | None, *, reason: str) -> None:
    if not asr_chat_hub or not device_id:
        return
    from deskbot_server.pb.mic_signal import build_mic_signal_pb

    payload = build_mic_signal_pb(mic="open")
    try:
        n = await asr_chat_hub.send(device_id, payload)
        logger.info(
            "[ASR] mic=open pb_single device_id=%s reason=%s delivered=%d req=%s",
            device_id,
            reason,
            n,
            payload.get("req"),
        )
    except Exception:
        logger.exception("[ASR] mic=open pb_single 下发失败 device_id=%s reason=%s", device_id, reason)


async def _run_asr_turn(
    websocket,
    *,
    pipeline: ChatService,
    audio_cfg: AudioConfig,
    session: ConnectionSession,
    pcm_segment: bytes,
    device_id: str | None,
    dp_broker: DevicePipelineBroker,
    registry: DeviceRegistry,
    asr_chat_hub: AsrChatHub | None = None,
    uplink_sample_rate: int | None = None,
    uplink_channels: int = 1,
    uplink_codec: str = "pcm16",
) -> None:
    request_id = _new_request_id()
    sample_rate = uplink_sample_rate or audio_cfg.sample_rate
    seg_duration_ms = int(len(pcm_segment) / 2 / sample_rate * 1000)
    t_asr_start = time.monotonic()
    asr_svc = AsrService()
    try:
        text = await asr_svc.transcribe(pcm_segment, sample_rate)
    except RuntimeError:
        text = await pipeline.asr(pcm_segment, sample_rate=sample_rate)
    t_asr_text = time.monotonic()
    asr_ms = _ms_between(t_asr_start, t_asr_text)
    if not text:
        logger.info(
            "[ASR] 结果为空 device_id=%s req=%s audio_ms=%d asr_ms=%s", device_id, request_id, seg_duration_ms, asr_ms
        )
        await _publish_asr_capture(
            dp_broker,
            device_id,
            request_id=request_id,
            pcm_segment=pcm_segment,
            sample_rate=sample_rate,
            asr_text=None,
            asr_ms=asr_ms,
            asr_valid=False,
            error="asr_empty",
            channels=uplink_channels,
            codec=uplink_codec,
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
        await _send_mic_open_signal(asr_chat_hub, device_id, reason="asr_empty")
        return
    try:
        asr_ok = asr_svc.is_valid_text(text)
    except RuntimeError:
        asr_ok = pipeline.is_valid_asr_text(text)
    if not asr_ok:
        logger.info(
            "[ASR] 结果被过滤 device_id=%s req=%s audio_ms=%d asr_ms=%s text=%r",
            device_id,
            request_id,
            seg_duration_ms,
            asr_ms,
            text,
        )
        await _publish_asr_capture(
            dp_broker,
            device_id,
            request_id=request_id,
            pcm_segment=pcm_segment,
            sample_rate=sample_rate,
            asr_text=text,
            asr_ms=asr_ms,
            asr_valid=False,
            error="asr_filtered",
            channels=uplink_channels,
            codec=uplink_codec,
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
        await _send_mic_open_signal(asr_chat_hub, device_id, reason="asr_filtered")
        return
    logger.info(
        "[ASR] 识别成功 device_id=%s req=%s audio_ms=%d asr_ms=%s text=%r",
        device_id,
        request_id,
        seg_duration_ms,
        asr_ms,
        text,
    )
    if device_id:
        LiveService().note_conversation_start(device_id)
    await _publish_asr_capture(
        dp_broker,
        device_id,
        request_id=request_id,
        pcm_segment=pcm_segment,
        sample_rate=sample_rate,
        asr_text=text,
        asr_ms=asr_ms,
        asr_valid=True,
        channels=uplink_channels,
        codec=uplink_codec,
    )
    downlink = WsDownlinkAdapter(websocket, settings=pipeline.settings, device_id=device_id, dp_broker=dp_broker)
    await downlink.emit_stage(
        "asr_done",
        request_id=request_id,
        send_client=False,
        event_fields={"asr_text": text, "asr_ms": asr_ms, "source": "asr"},
    )
    nod_done: asyncio.Event | None = None
    nod_task: asyncio.Task | None = None
    if asr_chat_hub is not None and device_id:
        nod_done, nod_task = start_llm_wait_nod_feedback(asr_chat_hub, device_id)

    async def _stop_nod_on_llm_error() -> None:
        """LLM 报错时立即停止点头，再播兜底 TTS，避免同时有点头和摇头。"""
        nonlocal nod_done, nod_task
        _done, _task = nod_done, nod_task
        nod_done, nod_task = None, None
        if _done is not None:
            await stop_llm_wait_nod_feedback(_done, _task)
            logger.info("[ASR] LLM 失败，已停止点头 device_id=%s req=%s", device_id, request_id)

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
            asr_chat_hub=asr_chat_hub,
            on_llm_error=_stop_nod_on_llm_error,
        )
    except Exception as exc:
        logger.exception("[ASR] 对话轮次异常 device_id=%s req=%s", device_id, request_id)
        await publish_ws_chat_turn(
            dp_broker,
            registry,
            device_id,
            source="asr",
            asr_text=text,
            t_asr_start=t_asr_start,
            t_asr_text=t_asr_text,
            flow={"status": "error", "error": str(exc), "t_llm_end": t_asr_text, "t_tts_end": t_asr_text},
            request_id=request_id,
        )
        return
    finally:
        if nod_done is not None:
            await stop_llm_wait_nod_feedback(nod_done, nod_task)
        if device_id:
            LiveService().note_conversation_end(device_id)
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


async def _dispatch_rom_flush(
    websocket,
    *,
    pipeline: ChatService,
    audio_cfg: AudioConfig,
    session: ConnectionSession,
    device_id: str | None,
    dp_broker: DevicePipelineBroker,
    registry: DeviceRegistry,
    asr_chat_hub: AsrChatHub,
    device_pb_only: bool,
    turn_task_holder: list,
) -> None:
    loop = asyncio.get_running_loop()
    flushed = await loop.run_in_executor(None, session.flush)
    if flushed is None:
        logger.info("[/asr_chat] flush 无有效语音段 device_id=%s（Silero 已丢弃静音）", device_id)
        return
    duration_ms = int(len(flushed.pcm) / 2 / max(1, flushed.sample_rate) * 1000)
    logger.info(
        "[/asr_chat] flush device_id=%s pcm_bytes=%d sr=%d ch=%d codec=%s duration_ms=%d",
        device_id,
        len(flushed.pcm),
        flushed.sample_rate,
        flushed.channels,
        flushed.codec,
        duration_ms,
    )
    if device_pb_only:
        await _schedule_asr_turn(
            websocket,
            pipeline=pipeline,
            audio_cfg=audio_cfg,
            session=session,
            pcm_segment=flushed.pcm,
            device_id=device_id,
            dp_broker=dp_broker,
            registry=registry,
            asr_chat_hub=asr_chat_hub,
            turn_task_holder=turn_task_holder,
            uplink_sample_rate=flushed.sample_rate,
            uplink_channels=flushed.channels,
            uplink_codec=flushed.codec,
        )
    else:
        await _run_asr_turn(
            websocket,
            pipeline=pipeline,
            audio_cfg=audio_cfg,
            session=session,
            pcm_segment=flushed.pcm,
            device_id=device_id,
            dp_broker=dp_broker,
            registry=registry,
            asr_chat_hub=asr_chat_hub,
            uplink_sample_rate=flushed.sample_rate,
            uplink_channels=flushed.channels,
            uplink_codec=flushed.codec,
        )


async def handle_asr_chat(
    websocket,
    pipeline: ChatService,
    audio_cfg: AudioConfig,
    device_id: str | None,
    registry: DeviceRegistry,
    dp_broker: DevicePipelineBroker,
    asr_chat_hub: AsrChatHub,
) -> None:
    """/asr_chat WS：音频/文本上行；可选 ``camera_frame`` + JPEG（``next_bin_len``）。

    相机帧仅服务端入库/调试预览，不因相机结果向本连接回写。
    """
    try:
        session = VadService().create_connection_session(pipeline)
    except RuntimeError:
        session = ConnectionSession(pipeline, audio_cfg)
    peer = WsUtils.peer_str(websocket)
    pending: PendingUplinkBinary | None = None
    turn_task_holder: list[asyncio.Task] = []
    device_pb_only = getattr(pipeline, "asr_chat_device_pb_only", False)
    camera_face_enabled = bool(device_id and CameraFaceService().is_configured())

    ensure_uplink_rate_stats_started()
    if device_id:
        await registry.connect(device_id, "asr_chat", websocket)
        await asr_chat_hub.attach(device_id, websocket)
        logger.info("[/asr_chat] 接入 device_id=%s peer=%s (已登记到 DeviceRegistry)", device_id, peer)
    else:
        logger.warning(
            "[/asr_chat] 接入缺失 device_id peer=%s —— 不会出现在 /api/devices 设备列表，"
            "请改用 ws://host:9000/asr_chat?device_id=<设备ID>",
            peer,
        )
    try:
        ready_ok = await WsUtils.safe_send(
            websocket, pack_ws_downlink_frame(_json_msg({"type": "ready", "device_id": device_id}))
        )
        logger.info("[/asr_chat] ready device_id=%s peer=%s sent=%s", device_id, peer, ready_ok)
        if not ready_ok:
            return
        if device_id:
            await deliver_boot_wake_scene(asr_chat_hub, device_id)

        async for message in websocket:
            attached_media: bytes | None = None
            try:
                # --- 等待中的 binary（上一帧 JSON 已声明 next_bin_len）---
                if pending is not None:
                    if not isinstance(message, (bytes, bytearray)):
                        logger.warning(
                            "[/asr_chat] device_id=%s 预期 %d 字节 binary，收到 JSON，丢弃", device_id, pending.length
                        )
                        pending = None
                        continue
                    payload = bytes(message)
                    if len(payload) != pending.length:
                        logger.warning(
                            "[/asr_chat] device_id=%s binary 长度不符 expected=%d got=%d kind=%s",
                            device_id,
                            pending.length,
                            len(payload),
                            pending.kind,
                        )
                        pending = None
                        continue
                    kind = pending.kind
                    codec = pending.codec
                    uplink_sr = pending.sample_rate
                    uplink_ch = pending.channels
                    uplink_frames = pending.opus_frames
                    uplink_flush = pending.flush
                    pending = None

                    if kind == "camera_frame":
                        spawn(
                            _ingest_asr_chat_camera_frame(
                                payload=payload,
                                device_id=device_id,
                                camera_face_enabled=camera_face_enabled,
                                enc="binary",
                            ),
                            name=f"asr_chat_camera:{device_id or '?'}",
                        )
                        continue

                    await _feed_rom_uplink(
                        payload,
                        codec,
                        session=session,
                        asr_chat_hub=asr_chat_hub,
                        device_id=device_id,
                        sample_rate=uplink_sr,
                        channels=uplink_ch,
                        opus_frames=uplink_frames,
                        websocket=websocket,
                        pipeline=pipeline,
                        audio_cfg=audio_cfg,
                        dp_broker=dp_broker,
                        registry=registry,
                        turn_task_holder=turn_task_holder,
                        device_pb_only=device_pb_only,
                    )
                    if uplink_flush:
                        await _dispatch_rom_flush(
                            websocket,
                            pipeline=pipeline,
                            audio_cfg=audio_cfg,
                            session=session,
                            device_id=device_id,
                            dp_broker=dp_broker,
                            registry=registry,
                            asr_chat_hub=asr_chat_hub,
                            device_pb_only=device_pb_only,
                            turn_task_holder=turn_task_holder,
                        )
                    continue

                # --- binary：新固件打包帧，或旧固件裸 audio ---
                if isinstance(message, (bytes, bytearray)):
                    payload = bytes(message)
                    frame = parse_packed_frame(payload)
                    if frame is not None:
                        data = frame.doc
                        attached_media = frame.bin
                    else:
                        await _feed_rom_uplink(
                            payload,
                            None,
                            session=session,
                            asr_chat_hub=asr_chat_hub,
                            device_id=device_id,
                            websocket=websocket,
                            pipeline=pipeline,
                            audio_cfg=audio_cfg,
                            dp_broker=dp_broker,
                            registry=registry,
                            turn_task_holder=turn_task_holder,
                            device_pb_only=device_pb_only,
                        )
                        continue
                else:
                    data = json.loads(message)
                msg_type = data.get("type")

                if msg_type == "boot_connect":
                    if device_id:
                        await deliver_boot_wake_scene(asr_chat_hub, device_id)
                    continue

                if msg_type == "pb_ack":
                    norm = _normalize_incoming_pb_ack(data)
                    if norm is not None and device_id:
                        note_uplink_ack(device_id)
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
                        logger.info("[pb_ack] 已解析但连接无 device_id，未入库 peer=%s", peer)
                    continue

                if msg_type == "user_text":
                    ut = (data.get("text") or "").strip()
                    try:
                        text_ok = bool(ut) and AsrService().is_valid_text(ut)
                    except RuntimeError:
                        text_ok = bool(ut) and pipeline.is_valid_asr_text(ut)
                    if not text_ok:
                        continue
                    request_id = _new_request_id()
                    t_asr_start = time.monotonic()
                    t_asr_text = time.monotonic()
                    text_downlink = WsDownlinkAdapter(
                        websocket, settings=pipeline.settings, device_id=device_id, dp_broker=dp_broker
                    )
                    await text_downlink.emit_stage(
                        "asr_done",
                        request_id=request_id,
                        send_client=False,
                        event_fields={"asr_text": ut, "asr_ms": 0, "source": "text"},
                    )
                    if device_id:
                        LiveService().note_conversation_start(device_id)
                    nod_done, nod_task = start_llm_wait_nod_feedback(asr_chat_hub, device_id)
                    try:
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
                    finally:
                        await stop_llm_wait_nod_feedback(nod_done, nod_task)
                        if device_id:
                            LiveService().note_conversation_end(device_id)
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
                    # 兼容旧固件独立 type=flush；新固件用 audio.flush=1。
                    await _dispatch_rom_flush(
                        websocket,
                        pipeline=pipeline,
                        audio_cfg=audio_cfg,
                        session=session,
                        device_id=device_id,
                        dp_broker=dp_broker,
                        registry=registry,
                        asr_chat_hub=asr_chat_hub,
                        device_pb_only=device_pb_only,
                        turn_task_holder=turn_task_holder,
                    )
                    continue

                if msg_type == "audio_cancel":
                    session.cancel_rom_uplink()
                    continue

                if msg_type == "camera_frame":
                    raw_b64 = data.get("data")
                    if raw_b64:
                        try:
                            payload = base64.b64decode(raw_b64)
                        except Exception:
                            logger.warning("[/asr_chat] camera_frame base64 解码失败 device_id=%s", device_id)
                            continue
                        spawn(
                            _ingest_asr_chat_camera_frame(
                                payload=payload,
                                device_id=device_id,
                                camera_face_enabled=camera_face_enabled,
                                enc="base64",
                            ),
                            name=f"asr_chat_camera:{device_id or '?'}",
                        )
                        continue
                    nbl = coerce_next_bin_len(data)
                    if nbl > 0:
                        if attached_media is not None:
                            if len(attached_media) != nbl:
                                logger.warning(
                                    "[/asr_chat] device_id=%s packed camera binary 长度不符 expected=%d got=%d",
                                    device_id,
                                    nbl,
                                    len(attached_media),
                                )
                                continue
                            spawn(
                                _ingest_asr_chat_camera_frame(
                                    payload=attached_media,
                                    device_id=device_id,
                                    camera_face_enabled=camera_face_enabled,
                                    enc="binary",
                                ),
                                name=f"asr_chat_camera:{device_id or '?'}",
                            )
                            continue
                        if pending is not None:
                            logger.warning(
                                "[/asr_chat] camera_frame 覆盖未完成的 pending device_id=%s old_len=%d new_len=%d",
                                device_id,
                                pending.length,
                                nbl,
                            )
                        pending = PendingUplinkBinary(kind="camera_frame", length=nbl)
                        continue
                    logger.warning("[/asr_chat] camera_frame 缺少 next_bin_len device_id=%s", device_id)
                    continue

                if msg_type == "audio":
                    nbl = coerce_next_bin_len(data)
                    want_flush = coerce_audio_flush(data)
                    if nbl > 0:
                        sr_raw = data.get("sr")
                        ch_raw = data.get("ch")
                        try:
                            uplink_sr = int(sr_raw) if sr_raw is not None else audio_cfg.sample_rate
                        except (TypeError, ValueError):
                            uplink_sr = audio_cfg.sample_rate
                        try:
                            uplink_ch = int(ch_raw) if ch_raw is not None else audio_cfg.channels
                        except (TypeError, ValueError):
                            uplink_ch = audio_cfg.channels
                        codec = data.get("codec")
                        uplink_frames = coerce_opus_frames(data)
                        if attached_media is not None:
                            if len(attached_media) != nbl:
                                logger.warning(
                                    "[/asr_chat] device_id=%s packed audio binary 长度不符 expected=%d got=%d",
                                    device_id,
                                    nbl,
                                    len(attached_media),
                                )
                                continue
                            await _feed_rom_uplink(
                                attached_media,
                                codec,
                                session=session,
                                asr_chat_hub=asr_chat_hub,
                                device_id=device_id,
                                sample_rate=uplink_sr,
                                channels=uplink_ch,
                                opus_frames=uplink_frames,
                                websocket=websocket,
                                pipeline=pipeline,
                                audio_cfg=audio_cfg,
                                dp_broker=dp_broker,
                                registry=registry,
                                turn_task_holder=turn_task_holder,
                                device_pb_only=device_pb_only,
                            )
                            if want_flush:
                                await _dispatch_rom_flush(
                                    websocket,
                                    pipeline=pipeline,
                                    audio_cfg=audio_cfg,
                                    session=session,
                                    device_id=device_id,
                                    dp_broker=dp_broker,
                                    registry=registry,
                                    asr_chat_hub=asr_chat_hub,
                                    device_pb_only=device_pb_only,
                                    turn_task_holder=turn_task_holder,
                                )
                            continue
                        pending = PendingUplinkBinary(
                            kind="audio",
                            length=nbl,
                            codec=codec,
                            sample_rate=uplink_sr,
                            channels=uplink_ch,
                            opus_frames=uplink_frames,
                            flush=want_flush,
                        )
                        continue
                    raw_b64 = data.get("data")
                    if raw_b64:
                        payload = base64.b64decode(raw_b64)
                        codec = data.get("codec")
                        sr_raw = data.get("sr")
                        ch_raw = data.get("ch")
                        try:
                            uplink_sr = int(sr_raw) if sr_raw is not None else None
                        except (TypeError, ValueError):
                            uplink_sr = None
                        try:
                            uplink_ch = int(ch_raw) if ch_raw is not None else None
                        except (TypeError, ValueError):
                            uplink_ch = None
                        await _feed_rom_uplink(
                            payload,
                            codec,
                            session=session,
                            asr_chat_hub=asr_chat_hub,
                            device_id=device_id,
                            sample_rate=uplink_sr,
                            channels=uplink_ch,
                            websocket=websocket,
                            pipeline=pipeline,
                            audio_cfg=audio_cfg,
                            dp_broker=dp_broker,
                            registry=registry,
                            turn_task_holder=turn_task_holder,
                            device_pb_only=device_pb_only,
                        )
                    if want_flush:
                        await _dispatch_rom_flush(
                            websocket,
                            pipeline=pipeline,
                            audio_cfg=audio_cfg,
                            session=session,
                            device_id=device_id,
                            dp_broker=dp_broker,
                            registry=registry,
                            asr_chat_hub=asr_chat_hub,
                            device_pb_only=device_pb_only,
                            turn_task_holder=turn_task_holder,
                        )
                    continue

            except Exception as exc:
                logger.exception("处理客户端消息失败: %s", format_exc_detail(exc))
    except ConnectionClosed as closed:
        logger.info("WebSocket 已关闭: %s", closed)
    finally:
        if device_id:
            remove_device(device_id)
            await asr_chat_hub.detach(device_id, websocket)
            await registry.disconnect(websocket)
