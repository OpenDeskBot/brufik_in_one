#!/usr/bin/env bash
# deskbot-server 统一启动脚本（Linux / macOS / Windows Git Bash）
#
# 用法:
#   ./start.sh                  安装/更新依赖并启动（默认）
#   SKIP_SETUP=1 ./start.sh     仅启动，不装依赖
#   SETUP_ONLY=1 ./start.sh     只准备 venv/依赖，不启动进程
#   FAST_START=1 ./start.sh     跳过 pip 安装（venv 须已完整），然后启动
#
# 环境变量:
#   PYTHON_BIN=             创建 venv 时使用的 Python（默认自动查找 >= 3.11）
#   USE_CPU_TORCH=1         安装 CPU 版 torch（默认 1）
#   SKIP_SYSTEM_CHECK=1     跳过 ffmpeg 等系统依赖警告

set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
# shellcheck source=/dev/null
source "$ROOT/../scripts/platform.sh"

REQUIRED_PY_MM="${REQUIRED_PY_MM:-3.11}"
USE_CPU_TORCH="${USE_CPU_TORCH:-1}"
FAST_START="${FAST_START:-0}"
SKIP_SETUP="${SKIP_SETUP:-0}"
SETUP_ONLY="${SETUP_ONLY:-0}"
TORCH_WHEEL_DIR="${TORCH_WHEEL_DIR:-$HOME/.cache/torch-wheels}"
VENV_DIR="$ROOT/.venv"

if [[ -f .env ]]; then
  echo "加载 .env ..."
  set -a
  # shellcheck source=/dev/null
  source ".env"
  set +a
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
  if "$py" -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple 2>/dev/null; then
    :
  else
    export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
  fi
}

run_deskbot_server() {
  local py
  py="$(resolve_venv_python)"
  echo "启动 deskbot-server..."
  exec "$py" -m deskbot_server
}

if [[ "$SKIP_SETUP" == "1" ]]; then
  run_deskbot_server
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

echo "检查系统依赖..."
platform_warn_system_deps

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
  if ! "$VPY" -c "import numpy, websockets, yaml, webrtcvad, openai, opuslib_next, torch, torchaudio, funasr, croniter, deskbot_server" >/dev/null 2>&1; then
    echo "当前 .venv 依赖不完整（常见是缺少 torch 或未 pip install -e .）。" >&2
    echo "请执行 ./start.sh（不设 FAST_START/SKIP_SETUP）后重试。" >&2
    exit 1
  fi
else
  configure_pip_index "$VPY"

  if [[ "$USE_CPU_TORCH" == "1" ]]; then
    platform_install_cpu_torch "$VPY" "$REQUIRED_PY_MM" "$TORCH_WHEEL_DIR"
  fi

  echo "安装项目依赖..."
  "$VPY" -m pip install -r requirements.txt --default-timeout=1000 --cache-dir="${HOME}/.cache/pip" \
    ${PIP_INDEX_URL:+--index-url "$PIP_INDEX_URL"} || {
    echo "依赖安装失败：请确认 Python >= ${REQUIRED_PY_MM} 且网络可访问 PyPI。" >&2
    exit 1
  }
  "$VPY" -m pip install -e . --no-deps ${PIP_INDEX_URL:+--index-url "$PIP_INDEX_URL"}
fi

if [[ "$SETUP_ONLY" == "1" ]]; then
  echo "deskbot-server 环境已就绪（SETUP_ONLY=1，未启动）。"
  exit 0
fi

run_deskbot_server
