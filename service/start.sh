#!/usr/bin/env bash
# 本地一键：校验 Python → 准备 deskbot-server venv → 启动主服务（可选 Flask 调试台）
# 支持 Linux / macOS / Windows Git Bash（不调用 apt/yum，系统依赖请自行安装）
#
# 用法（在仓库根目录）:
#   ./start.sh
#
# 可选环境变量:
#   PYTHON_VERSION=3.11     目标 Python 主次版本
#   PYTHON_BIN=             显式指定 Python 可执行文件（跳过自动查找）
#   SKIP_SETUP=1            跳过 venv/依赖安装，仅启动服务
#   FAST_START=1            跳过 pip 安装（venv 须已完整）；未设置时若依赖已就绪也会自动跳过
#   DESKBOT_START_WEB=1     同时启动 Flask 调试台（默认 1，DESKBOT_WEB_PORT=5050）
#   DESKBOT_START_WEB=0     不启动调试台
#   SKIP_MODEL_DOWNLOAD=1   跳过 ASR / 人脸模型自动下载
#   USE_CPU_TORCH=1         deskbot-server 使用 CPU 版 torch（默认 1）
#   SKIP_SYSTEM_CHECK=1     跳过 ffmpeg 等系统依赖警告

set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
BS="$ROOT/deskbot-server"
# shellcheck source=/dev/null
source "$ROOT/scripts/platform.sh"

_parse_python_version() {
  local v="${1:-3.11}"
  PY_MAJOR="${v%%.*}"
  local rest="${v#*.}"
  PY_MINOR="${rest%%.*}"
  PYTHON_MM="${PY_MAJOR}.${PY_MINOR}"
}
_parse_python_version "${PYTHON_VERSION:-3.11}"

ensure_python() {
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    if platform_python_version_ok "$PYTHON_BIN" "$PY_MAJOR" "$PY_MINOR"; then
      PYTHON_BIN="$(platform_resolve_python_executable "$PYTHON_BIN")"
      echo "Python: $PYTHON_BIN"
      export PYTHON_BIN
      return 0
    fi
    echo "PYTHON_BIN=$PYTHON_BIN 不满足 Python ${PYTHON_MM}。" >&2
    exit 1
  fi

  if PYTHON_BIN="$(platform_find_python "$PYTHON_MM")"; then
    echo "Python: $PYTHON_BIN"
    export PYTHON_BIN
    return 0
  fi

  echo "未找到 Python ${PYTHON_MM}。" >&2
  if platform_is_windows; then
    echo "Windows 请从 https://www.python.org/downloads/ 安装，或使用: py -${PYTHON_MM}" >&2
  else
    echo "请用系统包管理器安装 python${PYTHON_MM} 与 venv 支持后重试。" >&2
  fi
  echo "也可显式指定: PYTHON_BIN=/path/to/python ./start.sh" >&2
  exit 1
}

setup_deskbot_venv() {
  echo "[setup] deskbot-server venv（FunASR + torch ${PYTHON_MM} + requirements.txt）..."
  (
    cd "$BS"
    export PYTHON_BIN
    export SETUP_ONLY=1
    export FAST_START="${FAST_START:-0}"
    export USE_CPU_TORCH="${USE_CPU_TORCH:-1}"
    platform_run_sh "$BS/start.sh"
  )
}

venvs_look_ready() {
  local bs_py
  bs_py="$(platform_venv_python "$BS" 2>/dev/null)" || return 1
  "$bs_py" -c "import numpy, websockets, yaml, webrtcvad, openai, opuslib_next, torch, torchaudio, funasr, croniter, deskbot_server" >/dev/null 2>&1 || return 1
}

ensure_local_scripts() {
  if [[ ! -f "$BS/start.sh" ]]; then
    echo "缺少脚本: $BS/start.sh" >&2
    exit 1
  fi
}

ASR_MODEL_DIR="$BS/models/SenseVoiceSmall"
FACE_MODEL_PATH="$BS/models/mediapipe/face_landmarker.task"
FACE_MODEL_URL="https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"

asr_model_ready() {
  local py
  py="$(deskbot_venv_python 2>/dev/null)" || return 1
  "$py" "$BS/scripts/check_asr_model.py" "$ASR_MODEL_DIR"
}

face_model_ready() {
  [[ -f "$FACE_MODEL_PATH" ]]
}

deskbot_venv_python() {
  platform_venv_python "$BS" || {
    echo "未找到 deskbot-server/.venv，请先完成 setup（不要设 SKIP_SETUP=1）。" >&2
    exit 1
  }
}

ensure_deskbot_env() {
  if [[ ! -f "$BS/.env" && -f "$BS/.env.example" ]]; then
    cp "$BS/.env.example" "$BS/.env"
    echo "[setup] 已从 .env.example 创建 deskbot-server/.env"
    echo "[setup] 请编辑 deskbot-server/.env 并填写 LLM_API_KEY（必填）"
  fi

  if [[ -f "$BS/.env" ]]; then
    # shellcheck source=/dev/null
    set -a && source "$BS/.env" && set +a
  fi

  if [[ -z "${LLM_API_KEY:-}${DASHSCOPE_API_KEY:-}${QWEN_API_KEY:-}" ]]; then
    echo "[warn] 未设置 LLM_API_KEY（或 DASHSCOPE_API_KEY / QWEN_API_KEY），语音对话将无法调用大模型。" >&2
    echo "[warn] 请编辑 deskbot-server/.env 后重启。" >&2
  fi
}

download_asr_model() {
  echo "[setup] 下载 SenseVoiceSmall ASR 模型（约 900MB，首次较慢）..."
  local py
  py="$(deskbot_venv_python)"
  "$py" -m pip install -U modelscope
  "$py" "$BS/scripts/download_model.py"
}

download_face_model() {
  echo "[setup] 下载 MediaPipe 人脸模型（约 3.6MB）..."
  mkdir -p "$(dirname "$FACE_MODEL_PATH")"
  if command -v curl >/dev/null 2>&1; then
    curl -L --fail -o "$FACE_MODEL_PATH" "$FACE_MODEL_URL"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$FACE_MODEL_PATH" "$FACE_MODEL_URL"
  else
    echo "[warn] 未找到 curl/wget，跳过人脸模型下载；camera_frame 人脸功能可能不可用。" >&2
    return 0
  fi
}

ensure_models() {
  if [[ "${SKIP_MODEL_DOWNLOAD:-0}" == "1" ]]; then
    echo "SKIP_MODEL_DOWNLOAD=1，跳过模型下载检查。"
    if ! asr_model_ready; then
      echo "ASR 模型缺失: $ASR_MODEL_DIR" >&2
      exit 1
    fi
    return 0
  fi

  if ! asr_model_ready; then
    download_asr_model
  else
    echo "[setup] ASR 模型已就绪: $ASR_MODEL_DIR"
  fi

  if ! face_model_ready; then
    download_face_model
  else
    echo "[setup] 人脸模型已就绪: $FACE_MODEL_PATH"
  fi
}

run_services() {
  trap 'trap - INT TERM EXIT; kill 0 2>/dev/null || true' INT TERM EXIT

  if [[ "${DESKBOT_START_WEB:-1}" == "1" ]]; then
    local web_port="${DESKBOT_WEB_PORT:-5050}"
    echo "[web] 启动 Flask 调试台 0.0.0.0:${web_port}（局域网 http://<本机IP>:${web_port}/）"
    (
      cd "$BS"
      # shellcheck source=/dev/null
      [[ -f .env ]] && set -a && source .env && set +a
      web_py="$(platform_venv_python "$BS")"
      export DESKBOT_WEB_HOST="0.0.0.0"
      export DESKBOT_WEB_PORT="${DESKBOT_WEB_PORT:-5050}"
      exec "$web_py" -m deskbot_server.web
    ) &
  fi

  echo "[1/1] 启动 deskbot-server ($BS) ..."
  cd "$BS"
  exec env SKIP_SETUP=1 bash "$BS/start.sh"
}

# --- main ---
export DESKBOT_START_WEB="${DESKBOT_START_WEB:-1}"

ensure_python
platform_warn_system_deps
ensure_local_scripts

if [[ "${SKIP_SETUP:-0}" != "1" ]]; then
  if [[ "${FAST_START:-0}" != "1" ]] && venvs_look_ready; then
    echo "[setup] 检测到 venv 依赖已就绪，跳过 pip 安装（等同 FAST_START=1）。"
    export FAST_START=1
  fi
  setup_deskbot_venv
else
  echo "SKIP_SETUP=1，跳过 venv/依赖安装。"
fi

ensure_deskbot_env
ensure_models

run_services
