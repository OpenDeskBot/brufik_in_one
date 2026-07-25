"""应用服务层：ASR / VAD / 人脸 / TTS / LLM 等（单例）。"""

from __future__ import annotations

__all__ = [
    "AsrService",
    "CameraFaceService",
    "ChatAppService",
    "LlmService",
    "PipelineService",
    "TtsService",
    "VadService",
]


def __getattr__(name: str):
    if name == "AsrService":
        from deskbot_server.service.asr_service import AsrService

        return AsrService
    if name == "ChatAppService":
        from deskbot_server.service.chat_app_service import ChatAppService

        return ChatAppService
    if name == "CameraFaceService":
        from deskbot_server.service.camera_face_service import CameraFaceService

        return CameraFaceService
    if name == "LlmService":
        from deskbot_server.service.llm_service import LlmService

        return LlmService
    if name == "PipelineService":
        from deskbot_server.service.pipeline_service import PipelineService

        return PipelineService
    if name == "TtsService":
        from deskbot_server.service.tts_service import TtsService

        return TtsService
    if name == "VadService":
        from deskbot_server.service.vad_service import VadService

        return VadService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
