#!/usr/bin/env bash
# paddlespeech-server 统一启动脚本（Linux / macOS / Windows Git Bash）
#
# 用法:
#   ./start.sh                  安装/更新依赖并启动（默认）
#   SKIP_SETUP=1 ./start.sh     仅启动，不装依赖
#   SETUP_ONLY=1 ./start.sh     只准备 venv/依赖，不启动进程
#   FAST_START=1 ./start.sh     跳过 pip 安装（venv 须已完整），然后启动
#
# 环境变量:
#   PYTHON_BIN=             创建 venv 时使用的 Python（默认自动查找 >= 3.11）
#   PADDLE_VENV=            虚拟环境根路径，默认 ./.venv
#   PADDLESPEECH_CONFIG=    yaml 路径，默认 ./conf/tts_online_application.yaml
#   PIP_INDEX_URL=          pip 镜像（可选）

set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
# shellcheck source=/dev/null
source "$ROOT/../scripts/platform.sh"

REQUIRED_PY_MM="${REQUIRED_PY_MM:-3.11}"
FAST_START="${FAST_START:-0}"
SKIP_SETUP="${SKIP_SETUP:-0}"
SETUP_ONLY="${SETUP_ONLY:-0}"
VENV_DIR="${PADDLE_VENV:-$ROOT/.venv}"
CONFIG="${PADDLESPEECH_CONFIG:-$ROOT/conf/tts_online_application.yaml}"

if [[ -f .env ]]; then
  echo "加载 .env ..."
  set -a
  # shellcheck source=/dev/null
  source ".env"
  set +a
  VENV_DIR="${PADDLE_VENV:-$VENV_DIR}"
  CONFIG="${PADDLESPEECH_CONFIG:-$CONFIG}"
fi

resolve_venv_python() {
  platform_venv_python "$ROOT" || {
    echo "未找到 .venv，请先运行: ./start.sh（不要设 SKIP_SETUP=1）" >&2
    exit 1
  }
}

require_python() {
  local bin="$1"
  local label="${2:-$bin}"
  if [[ ! -f "$bin" ]] && ! command -v "$bin" >/dev/null 2>&1; then
    echo "未找到 ${label}。" >&2
    return 1
  fi
  local ver req_major req_minor
  ver="$("$bin" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
  req_major="${REQUIRED_PY_MM%%.*}"
  req_minor="${REQUIRED_PY_MM#*.}"
  req_minor="${req_minor%%.*}"
  if ! platform_python_version_ok "$bin" "$req_major" "$req_minor"; then
    echo "需要 Python >= ${REQUIRED_PY_MM}，当前 ${label}=${ver}。" >&2
    if platform_is_windows; then
      echo "Windows: 请安装 Python ${REQUIRED_PY_MM}+，或 PYTHON_BIN=\"py -${REQUIRED_PY_MM}\" ./start.sh" >&2
    else
      echo "或指定解释器: PYTHON_BIN=python3.11 ./start.sh" >&2
    fi
    return 1
  fi
  echo "Python: ${label} (${ver})"
}

configure_pip_index() {
  local py="$1"
  "$py" -m pip install --upgrade pip
  if [[ -n "${PIP_INDEX_URL:-}" ]]; then
    "$py" -m pip config set global.index-url "$PIP_INDEX_URL" 2>/dev/null || true
  elif "$py" -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple 2>/dev/null; then
    :
  else
    export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
  fi
}

ensure_nltk_tagger_eng() {
  local tagger_dir="$VENV_DIR/nltk_data/taggers/averaged_perceptron_tagger_eng"
  if [[ -f "$tagger_dir/averaged_perceptron_tagger_eng.weights.json" ]]; then
    return 0
  fi
  echo "准备 NLTK 英文标注数据（mix TTS 需要）..."
  mkdir -p "$VENV_DIR/nltk_data/taggers"
  (
    cd "$VENV_DIR/nltk_data/taggers"
    curl -fsSL -o averaged_perceptron_tagger_eng.zip \
      "https://raw.githubusercontent.com/nltk/nltk_data/gh-pages/packages/taggers/averaged_perceptron_tagger_eng.zip"
    unzip -o -q averaged_perceptron_tagger_eng.zip
    rm -f averaged_perceptron_tagger_eng.zip
  ) || echo "警告: NLTK 数据下载失败，mix 英文段可能无法合成。" >&2
}

run_paddlespeech_server() {
  local py
  py="$(resolve_venv_python)"
  VENV_DIR="${PADDLE_VENV:-$ROOT/.venv}"
  ensure_nltk_tagger_eng
  if [[ ! -f "$CONFIG" ]]; then
    echo "未找到配置: $CONFIG" >&2
    exit 1
  fi
  export PADDLESPEECH_HOST="${PADDLESPEECH_HOST:-0.0.0.0}"
  export PADDLESPEECH_PORT="${PADDLESPEECH_PORT:-8092}"
  echo "PaddleSpeech (含音素 WS): $py -m paddlespeech_server --config_file $CONFIG"
  echo "  监听 0.0.0.0:${PADDLESPEECH_PORT}（局域网 ws://<本机IP>:${PADDLESPEECH_PORT}/...）"
  echo "  官方流式: /paddlespeech/tts/streaming"
  echo "  音素对齐: /paddlespeech/tts/streaming_phoneme"
  exec env PADDLESPEECH_HOST="$PADDLESPEECH_HOST" PADDLESPEECH_PORT="$PADDLESPEECH_PORT" \
    "$py" -m paddlespeech_server --config_file "$CONFIG"
}

if [[ "$SKIP_SETUP" == "1" ]]; then
  run_paddlespeech_server
fi

if [[ -z "${PYTHON_BIN:-}" ]]; then
  PYTHON_BIN="$(platform_find_python "$REQUIRED_PY_MM")" || {
    echo "未找到 Python >= ${REQUIRED_PY_MM}。" >&2
    exit 1
  }
else
  PYTHON_BIN="$(platform_resolve_python_executable "$PYTHON_BIN")" || {
    echo "无法运行 PYTHON_BIN=$PYTHON_BIN" >&2
    exit 1
  }
fi
export PYTHON_BIN

require_python "$PYTHON_BIN" "$PYTHON_BIN"

echo "准备 Python 虚拟环境..."
if [[ ! -d "$VENV_DIR" ]]; then
  echo "未检测到 .venv，正在创建..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

VPY="$(platform_venv_python "$ROOT")" || {
  echo "创建 .venv 失败，请确认 Python 自带 venv 模块。" >&2
  exit 1
}

require_python "$VPY" "venv python"

if [[ "$FAST_START" == "1" ]]; then
  echo "FAST_START=1，跳过依赖安装。"
  if ! "$VPY" -c "import numpy, paddlespeech, paddlespeech_server" >/dev/null 2>&1; then
    echo "当前 .venv 依赖不完整（常见是未 pip install -e .）。" >&2
    echo "请执行 ./start.sh（不设 FAST_START/SKIP_SETUP）后重试。" >&2
    exit 1
  fi
else
  configure_pip_index "$VPY"

  echo "安装 PaddleSpeech 依赖（首次较慢，会下载 ONNX 模型）..."
  "$VPY" -m pip install -r requirements.txt --default-timeout=1000 --cache-dir="${HOME}/.cache/pip" \
    ${PIP_INDEX_URL:+--index-url "$PIP_INDEX_URL"} || {
    echo "依赖安装失败：请确认 Python >= ${REQUIRED_PY_MM} 且网络可访问 PyPI。" >&2
    exit 1
  }
  "$VPY" -m pip install -e . --no-deps ${PIP_INDEX_URL:+--index-url "$PIP_INDEX_URL"}
fi

ensure_nltk_tagger_eng

if [[ "$SETUP_ONLY" == "1" ]]; then
  echo "paddlespeech-server 环境已就绪（SETUP_ONLY=1，未启动）。"
  exit 0
fi

run_paddlespeech_server
