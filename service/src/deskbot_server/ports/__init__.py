"""端口层：Protocol 接口定义，供 service 和 infrastructure 依赖。"""

from deskbot_server.ports.asr import AsrPort
from deskbot_server.ports.downlink import DownlinkPort, PipelineEventsPort
from deskbot_server.ports.llm import LlmPort
from deskbot_server.ports.tts import PhonemeSegment, TtsPort

__all__ = ["AsrPort", "DownlinkPort", "LlmPort", "PhonemeSegment", "PipelineEventsPort", "TtsPort"]
