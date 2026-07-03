from __future__ import annotations

import asyncio
import logging
import os

import websockets

from deskbot_server.config import load_config
from deskbot_server.debug_prefs_store import apply_debug_prefs_from_config
from deskbot_server.constants import CAMERA_VIEW_PATH, DEVICE_PIPELINE_PATH
from deskbot_server.core.settings import AppSettings
from deskbot_server.env import load_dotenv
from deskbot_server.pipeline.audio import AudioConfig
from deskbot_server.vision.undistort import build_camera_face_runtime
from deskbot_server.application.camera_broker import CameraImageBroker
from deskbot_server.ws.asr_chat_hub import AsrChatHub, PbIdleSilenceServoAfterDownlink
from deskbot_server.ws.device_pipeline import DevicePipelineBroker
from deskbot_server.ws.http11_compat import patch_websockets_http11_for_rest_api
from deskbot_server.ws.http_api import _build_http_request_handler
from deskbot_server.ws.registry import DeviceRegistry
from deskbot_server.core.concurrency import configure_concurrency
from deskbot_server.application.scheduled_task_scheduler import ScheduledTaskScheduler
from deskbot_server.infrastructure.bootstrap import build_chat_service
from deskbot_server.ws.pb_idle_registry import set_pb_idle_hub
from deskbot_server.ws.router import handle_client
from deskbot_server.ws.ws_send import _safe_send

logger = logging.getLogger("deskbot-server")

async def main():
    load_dotenv()
    from deskbot_server.db import init_database
    from deskbot_server.db.engine import default_db_path

    init_database()
    logger.info("[server] auth DB ready path=%s", default_db_path())
    config = load_config(os.environ.get("DESKBOT_SERVER_CONFIG", "config.yaml"))
    apply_debug_prefs_from_config(config)
    app_settings = AppSettings.from_config(config)
    audio_cfg = AudioConfig(
        input_codec=app_settings.audio.input_codec,
        sample_rate=app_settings.audio.sample_rate,
        channels=app_settings.audio.channels,
        min_speech_ms=app_settings.vad.min_speech_ms,
        max_silence_ms=app_settings.vad.max_silence_ms,
        pre_speech_ms=app_settings.vad.pre_speech_ms,
        silero_model_path=app_settings.vad.silero_model_path,
        silero_threshold=app_settings.vad.silero_threshold,
        silero_threshold_low=app_settings.vad.silero_threshold_low,
    )
    logger.info(
        "[VAD/AUDIO] codec=%s sample_rate=%d channels=%d | silero "
        "min_speech_ms=%d max_silence_ms=%d pre_speech_ms=%d "
        "threshold=%.2f threshold_low=%.2f | "
        "asr_text_filter: min_text_len=%s min_chinese_ratio=%s",
        audio_cfg.input_codec,
        audio_cfg.sample_rate,
        audio_cfg.channels,
        audio_cfg.min_speech_ms,
        audio_cfg.max_silence_ms,
        audio_cfg.pre_speech_ms,
        audio_cfg.silero_threshold,
        audio_cfg.silero_threshold_low,
        config.get("asr", {}).get("text_filter", {}).get("min_text_len"),
        config.get("asr", {}).get("text_filter", {}).get("min_chinese_ratio"),
    )
    configure_concurrency(
        max_concurrent_asr=app_settings.server.max_concurrent_asr,
        max_concurrent_face_infer=app_settings.server.max_concurrent_face_infer,
    )
    pipeline = build_chat_service(config)
    device_pipeline_broker = DevicePipelineBroker()
    registry = DeviceRegistry()
    asr_chat_hub = AsrChatHub(
        device_pb_only=pipeline.asr_chat_device_pb_only,
        pipeline_broker=device_pipeline_broker,
    )
    # 自动休眠（sleep_snore 场景）暂关闭；恢复时取消下方注释块。
    # idle_snore_sec = app_settings.server.pb_idle_snore_sec
    # sn_scene = app_settings.server.pb_idle_snore_scene
    # if idle_snore_sec > 0:
    #     if not sn_scene:
    #         sn_scene = "sleep_snore"
    #     asr_chat_hub.pb_idle_snore = PbIdleSnoreAfterDownlink(
    #         asr_chat_hub,
    #         idle_sec=idle_snore_sec,
    #         scene_name=sn_scene,
    #     )
    #     logger.info(
    #         "[server] pb_idle_snore: 距上次成功下行 %.1fs 无新数据则 opportunistic 下发场景 %r",
    #         idle_snore_sec,
    #         sn_scene,
    #     )
    idle_silence_sec = app_settings.server.pb_idle_silence_sec
    if idle_silence_sec > 0:
        asr_chat_hub.pb_idle_silence = PbIdleSilenceServoAfterDownlink(
            asr_chat_hub,
            idle_sec=idle_silence_sec,
        )
        logger.info(
            "[server] pb_idle_silence: 距上次 pb 下行 %.1fs 无新数据则下发低头沉默 x=90 y=80 xm=0 ym=0",
            idle_silence_sec,
        )
    set_pb_idle_hub(asr_chat_hub)
    camera_image_broker = CameraImageBroker(send_fn=_safe_send)
    camera_face_runtime = build_camera_face_runtime(config)
    logger.info(
        "[server] send_face_info_to_asr_chat=%s（device_pb_only 为 true 时强制关闭；"
        "仅经 /asr_chat camera_frame 生效）",
        app_settings.server.send_face_info_to_asr_chat,
    )

    host = app_settings.server.host
    port = app_settings.server.port
    ws_path = app_settings.server.ws_path
    if not ws_path.startswith("/"):
        ws_path = f"/{ws_path}"
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(
        lambda _loop, context: logger.error(
            "未捕获事件循环异常: %s",
            context.get("message", "unknown"),
            exc_info=context.get("exception"),
        )
    )
    # 保活：ESP32/弱网若未及时回复协议层 ping，会触发 1011 keepalive ping timeout
    # DESKBOT_WS_PING_INTERVAL=0 或 none 表示关闭服务端主动 ping
    # 默认 20s/20s：过长 ping 超时会让僵尸 WS 长时间占位（含懒加载 MediaPipe）。
    ping_interval = app_settings.server.ws_ping_interval
    if ping_interval is not None:
        ping_interval = int(max(5, ping_interval))

    ping_timeout = int(max(5, app_settings.server.ws_ping_timeout))

    patch_websockets_http11_for_rest_api()

    http_handler = _build_http_request_handler(
        device_pipeline_broker,
        registry,
        asr_chat_hub=asr_chat_hub,
        chat=pipeline,
    )

    scheduler = ScheduledTaskScheduler(
        chat=pipeline,
        asr_chat_hub=asr_chat_hub,
        registry=registry,
        dp_broker=device_pipeline_broker,
    )
    scheduler.start()

    logger.info(
        "deskbot-server started on ws://%s:%s (asr=%s, camera_view=%s, "
        "device_pipeline=%s; "
        "ping_interval=%s ping_timeout=%s)",
        host,
        port,
        ws_path,
        CAMERA_VIEW_PATH,
        DEVICE_PIPELINE_PATH,
        ping_interval,
        ping_timeout,
    )
    async with websockets.serve(
        lambda ws: handle_client(
            ws,
            pipeline,
            audio_cfg,
            ws_path,
            device_pipeline_broker,
            registry,
            asr_chat_hub,
            camera_image_broker,
            camera_face_runtime,
        ),
        host,
        port,
        max_size=None,
        ping_interval=ping_interval,
        ping_timeout=ping_timeout,
        process_request=http_handler,
    ):
        await asyncio.Future()
