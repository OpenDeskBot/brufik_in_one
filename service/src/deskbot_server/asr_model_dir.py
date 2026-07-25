"""兼容导出：请改用 ``deskbot_server.infrastructure.asr.model_dir``。"""

from deskbot_server.infrastructure.asr.model_dir import *  # noqa: F403
from deskbot_server.infrastructure.asr.model_dir import (  # noqa: F401
    asr_model_dir_ready,
    ensure_asr_quant_onnx,
    has_quant_onnx,
    quant_onnx_path,
)
