#!/usr/bin/env bash
# A/B 对比：无相机上传 vs 有相机上传（各 MONITOR_SEC 秒）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${1:-/dev/ttyACM1}"
MONITOR_SEC="${2:-120}"
SERVER_LOG="${3:-}"
CONFIG="$ROOT/firmware/deskbot_config.h"
MONITOR="$ROOT/scripts/ab_monitor.py"

if [[ -z "$SERVER_LOG" ]]; then
  echo "usage: $0 [serial_port] [seconds] [server_log_path]"
  exit 1
fi

run_test() {
  local cam_flag="$1"
  local label="$2"
  echo "===== $label: DESKBOT_CAMERA_UPLINK_ENABLED=$cam_flag ====="
  sed -i "s/#define DESKBOT_CAMERA_UPLINK_ENABLED [01]/#define DESKBOT_CAMERA_UPLINK_ENABLED $cam_flag/" "$CONFIG"
  cd "$ROOT"
  pio run -e seeed_xiao_esp32s3 -t upload --upload-port "$PORT"
  echo "waiting 25s for boot+connect..."
  sleep 25
  python3 "$MONITOR" "$PORT" "$MONITOR_SEC" "$SERVER_LOG" "$label"
}

run_test 0 "TEST-A-NO-CAMERA"
echo ""
run_test 1 "TEST-B-WITH-CAMERA"
echo ""
echo "===== DONE: compare TEST-A vs TEST-B metrics above ====="
