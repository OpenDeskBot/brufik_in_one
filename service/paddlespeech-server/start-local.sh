#!/usr/bin/env bash
# 本地快速启动（等价于 SKIP_SETUP=1 ./start.sh）
# 支持 Linux / macOS / Windows Git Bash
#
# 环境变量:
#   PADDLE_VENV           虚拟环境根路径，默认本目录 ./.venv
#   PADDLESPEECH_CONFIG   yaml 路径，默认 ./conf/tts_online_application.yaml

set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$ROOT/.env"
  set +a
fi

export PADDLE_VENV="${PADDLE_VENV:-$ROOT/.venv}"
export PADDLESPEECH_CONFIG="${PADDLESPEECH_CONFIG:-$ROOT/conf/tts_online_application.yaml}"
export SKIP_SETUP=1

exec bash "$ROOT/start.sh"
