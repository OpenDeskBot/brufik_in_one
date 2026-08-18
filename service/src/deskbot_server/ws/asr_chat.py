from __future__ import annotations

import json
import logging
import time

from websockets.exceptions import ConnectionClosed

from deskbot_server.service.application.asr_chat_uplink import parse_packed_frame
from deskbot_server.service.application.boot_wake import deliver_boot_wake_scene
from deskbot_server.service.application.chat_service import ChatService
from deskbot_server.service.asr_service import AsrService
from deskbot_server.service.camera_face_service import CameraFaceService
from deskbot_server.service.pipeline.audio import AudioConfig, ConnectionSession
from deskbot_server.service.vad_service import VadService
from deskbot_server.utils.async_helpers import spawn
from deskbot_server.utils.util import _json_msg, format_exc_detail
from deskbot_server.utils.ws_utils import WsUtils
from deskbot_server.ws.asr_chat_hub import AsrChatHub
from deskbot_server.ws.registry import DeviceRegistry
from deskbot_server.ws.uplink_rate_stats import (
    ensure_uplink_rate_stats_started,
    note_uplink_audio,
    note_uplink_camera,
    remove_device,
)

logger = logging.getLogger("deskbot-server")


async def _ingest_camera_frame(
    *, payload: bytes, device_id: str | None, camera_face_enabled: bool, enc: str = "binary"
) -> None:
    """异步处理摄像头帧，交给 CameraFaceService，不阻塞 WS 读循环。"""
    note_uplink_camera(device_id)
    nbytes = len(payload or b"")
    if not device_id or not camera_face_enabled:
        logger.info(
            "[camera] device_id=%s bytes=%d accepted=false reason=not_configured enc=%s",
            device_id or "-",
            nbytes,
            enc,
        )
        return
    await CameraFaceService().process(device_id, payload, frame_source="asr_chat", log_channel="/asr_chat")


async def _run_asr_and_reply(
    websocket,
    *,
    pipeline: ChatService,
    audio_cfg: AudioConfig,
    pcm_segment: bytes,
    device_id: str | None,
    asr_chat_hub: AsrChatHub,
    uplink_sample_rate: int | None = None,
    uplink_channels: int = 1,
    uplink_codec: str = "pcm16",
) -> None:
    """ASR 识别 → LLM → TTS/pb 下发（单轮对话核心链路）。"""
    from deskbot_server.infrastructure.ws.downlink_adapter import WsDownlinkAdapter
    from deskbot_server.service.application.ws_chat_turn import run_ws_chat_turn

    sample_rate = uplink_sample_rate or audio_cfg.sample_rate
    audio_ms = int(len(pcm_segment) / 2 / max(1, sample_rate) * 1000)
    request_id = f"asr_{int(time.time() * 1000) & 0xFFFFFFFF:08x}"
    t_asr_start = time.monotonic()

    # ASR 识别
    try:
        text = await AsrService().transcribe(pcm_segment, sample_rate)
    except RuntimeError:
        text = await pipeline.asr(pcm_segment, sample_rate=sample_rate)
    t_asr_text = time.monotonic()
    asr_ms = (t_asr_text - t_asr_start) * 1000

    if not text:
        logger.info(
            "[ASR] 结果为空 device_id=%s req=%s audio_ms=%d sr=%d ch=%d asr_ms=%.0f",
            device_id, request_id, audio_ms, sample_rate, uplink_channels, asr_ms,
        )
        return

    # 文本有效性过滤
    try:
        asr_ok = AsrService().is_valid_text(text)
    except RuntimeError:
        asr_ok = pipeline.is_valid_asr_text(text)
    if not asr_ok:
        logger.info("[ASR] 结果被过滤 device_id=%s req=%s asr_ms=%.0f text=%r", device_id, request_id, asr_ms, text)
        return

    logger.info(
        "[ASR] 识别成功 device_id=%s req=%s audio_ms=%d sr=%d asr_ms=%.0f text=%r",
        device_id, request_id, audio_ms, sample_rate, asr_ms, text,
    )

    # LLM + TTS 对话
    downlink = WsDownlinkAdapter(websocket, settings=pipeline.settings, device_id=device_id)
    try:
        flow = await run_ws_chat_turn(
            websocket,
            pipeline,
            text,
            request_id=request_id,
            device_id=device_id,
            asr_chat_hub=asr_chat_hub,
        )
        llm_text = flow.get("llm_text") or ""
        status = flow.get("status", "ok")
        error = flow.get("error")
        logger.info(
            "[ASR] 对话完成 device_id=%s req=%s status=%s llm_text=%r error=%s",
            device_id, request_id, status, llm_text[:120], error,
        )
    except Exception:
        logger.exception("[ASR] 对话轮次异常 device_id=%s req=%s", device_id, request_id)


async def _process_flush(
    websocket,
    *,
    pipeline: ChatService,
    audio_cfg: AudioConfig,
    session: ConnectionSession,
    device_id: str | None,
    asr_chat_hub: AsrChatHub,
) -> None:
    """flush VAD 缓冲并触发 ASR 轮次。"""
    import asyncio

    loop = asyncio.get_running_loop()
    flushed = await loop.run_in_executor(None, session.flush)
    if flushed is None:
        logger.info("[/asr_chat] flush 无有效语音段 device_id=%s", device_id)
        return
    duration_ms = int(len(flushed.pcm) / 2 / max(1, flushed.sample_rate) * 1000)
    logger.info(
        "[/asr_chat] flush device_id=%s pcm_bytes=%d sr=%d ch=%d duration_ms=%d",
        device_id,
        len(flushed.pcm),
        flushed.sample_rate,
        flushed.channels,
        duration_ms,
    )
    await _run_asr_and_reply(
        websocket,
        pipeline=pipeline,
        audio_cfg=audio_cfg,
        pcm_segment=flushed.pcm,
        device_id=device_id,
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
    asr_chat_hub: AsrChatHub,
) -> None:
    """/asr_chat WS：新固件打包帧上行（音频 + 摄像头）；pb_ack 流控。"""
    try:
        session = VadService().create_connection_session(pipeline)
    except RuntimeError:
        session = ConnectionSession(pipeline, audio_cfg)
    peer = WsUtils.peer_str(websocket)
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
        ready_json = _json_msg({"type": "ready", "device_id": device_id})
        if device_id:
            ready_ok = await asr_chat_hub.send_text(device_id, ready_json)
        else:
            ready_ok = await WsUtils.safe_send(websocket, ready_json)
        logger.info("[/asr_chat] ready device_id=%s peer=%s sent=%s", device_id, peer, ready_ok)
        if not ready_ok:
            return
        if device_id:
            await deliver_boot_wake_scene(asr_chat_hub, device_id)

        async for message in websocket:
            try:
                # ── binary：新固件打包帧 ──────────────────────
                if isinstance(message, (bytes, bytearray)):
                    payload = bytes(message)
                    frame = parse_packed_frame(payload)
                    if frame is None:
                        logger.warning("[/asr_chat] 无法解析打包帧 device_id=%s bytes=%d", device_id, len(payload))
                        continue
                    data = frame.doc
                    attached_media = frame.bin
                    msg_type = data.get("type")

                    if msg_type == "audio":
                        if not attached_media:
                            logger.warning("[/asr_chat] audio 帧缺少 binary device_id=%s", device_id)
                            continue
                        codec = data.get("codec")
                        sr_raw = data.get("sr")
                        ch_raw = data.get("ch")
                        uplink_sr = int(sr_raw) if sr_raw is not None else audio_cfg.sample_rate
                        uplink_ch = int(ch_raw) if ch_raw is not None else audio_cfg.channels
                        note_uplink_audio(device_id)
                        utterance, _, _ = await session.feed_audio(
                            attached_media, codec, sample_rate=uplink_sr, channels=uplink_ch
                        )
                        # VAD 检测到完整语音段 → 触发 ASR
                        if utterance:
                            spawn(
                                _run_asr_and_reply(
                                    websocket,
                                    pipeline=pipeline,
                                    audio_cfg=audio_cfg,
                                    pcm_segment=utterance,
                                    device_id=device_id,
                                    asr_chat_hub=asr_chat_hub,
                                    uplink_sample_rate=uplink_sr,
                                    uplink_channels=uplink_ch,
                                    uplink_codec=codec,
                                ),
                                name=f"asr_turn:{device_id or '?'}",
                            )
                        # 音频帧带 flush 标志 → 强制 VAD 输出
                        if data.get("flush"):
                            await _process_flush(
                                websocket,
                                pipeline=pipeline,
                                audio_cfg=audio_cfg,
                                session=session,
                                device_id=device_id,
                                asr_chat_hub=asr_chat_hub,
                            )
                        continue

                    if msg_type in ("camera", "camera_frame"):
                        if attached_media:
                            spawn(
                                _ingest_camera_frame(
                                    payload=attached_media,
                                    device_id=device_id,
                                    camera_face_enabled=camera_face_enabled,
                                    enc="binary",
                                ),
                                name=f"asr_chat_camera:{device_id or '?'}",
                            )
                        continue

                    logger.debug("[/asr_chat] 未知打包帧 type=%r device_id=%s", msg_type, device_id)
                    continue

                # ── JSON 消息 ────────────────────────────────
                data = json.loads(message)
                msg_type = data.get("type")

                if msg_type == "boot_connect":
                    if device_id:
                        await deliver_boot_wake_scene(asr_chat_hub, device_id)
                    continue

                if msg_type == "pb_ack":
                    from deskbot_server.utils.util import _normalize_incoming_pb_ack

                    norm = _normalize_incoming_pb_ack(data)
                    if norm is not None and device_id:
                        await asr_chat_hub.ack(device_id, norm)
                    continue

                if msg_type == "flush":
                    await _process_flush(
                        websocket,
                        pipeline=pipeline,
                        audio_cfg=audio_cfg,
                        session=session,
                        device_id=device_id,
                        asr_chat_hub=asr_chat_hub,
                    )
                    continue

                if msg_type == "camera_frame":
                    raw_b64 = data.get("data")
                    if raw_b64:
                        try:
                            import base64

                            cam_payload = base64.b64decode(raw_b64)
                        except Exception:
                            logger.warning("[/asr_chat] camera_frame base64 解码失败 device_id=%s", device_id)
                            continue
                        spawn(
                            _ingest_camera_frame(
                                payload=cam_payload,
                                device_id=device_id,
                                camera_face_enabled=camera_face_enabled,
                                enc="base64",
                            ),
                            name=f"asr_chat_camera:{device_id or '?'}",
                        )
                    continue

                logger.debug("[/asr_chat] 未知消息类型 type=%r device_id=%s", msg_type, device_id)

            except Exception as exc:
                logger.exception("处理客户端消息失败: %s", format_exc_detail(exc))
    except ConnectionClosed as closed:
        logger.info("WebSocket 已关闭: %s", closed)
    finally:
        if device_id:
            remove_device(device_id)
            await asr_chat_hub.detach(device_id, websocket)
            await registry.disconnect(websocket)
