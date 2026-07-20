#!/usr/bin/env bash
# miot-ctl 一键安装：创建虚拟环境 + 下载 miloco-miot wheel + 安装依赖
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"
WHEEL_DIR="$ROOT/wheels"
VERSION="${MIOT_CTL_VERSION:-2026.7.3}"

info()  { printf '[miot-ctl] %s\n' "$*"; }
fail()  { printf '[miot-ctl] 错误: %s\n' "$*" >&2; exit 1; }

OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|amd64) ARCH_TAG="x86_64" ;;
  aarch64|arm64) ARCH_TAG="aarch64" ;;
  *) fail "不支持的架构: $ARCH" ;;
esac
case "$OS" in
  linux) PLATFORM="linux-${ARCH_TAG}"; WHEEL_TAG="manylinux_2_28_${ARCH_TAG}" ;;
  darwin)
    if [ "$ARCH_TAG" = "arm64" ]; then
      PLATFORM="darwin-arm64"; WHEEL_TAG="macosx_11_0_arm64"
    else
      PLATFORM="darwin-x86_64"; WHEEL_TAG="macosx_10_9_x86_64"
    fi
    ;;
  *) fail "不支持的操作系统: $OS（请用 Linux / macOS）" ;;
esac

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    UV=uv; return
  fi
  if [ -x "$HOME/.local/bin/uv" ]; then
    UV="$HOME/.local/bin/uv"; return
  fi
  info "未找到 uv，正在安装..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null 2>&1 || fail "uv 安装失败"
  UV=uv
}

download_wheel() {
  mkdir -p "$WHEEL_DIR"

  # 已有任意版本 wheel
  if compgen -G "$WHEEL_DIR/miloco_miot-*.whl" >/dev/null; then
    info "使用已有 wheel: $(ls "$WHEEL_DIR"/miloco_miot-*.whl | head -1)"
    return
  fi

  local wheel="miloco_miot-${VERSION}-py3-none-${WHEEL_TAG}.whl"
  local dest="$WHEEL_DIR/$wheel"

  # 本机 Miloco 安装缓存（解压后的 wheel）
  local cached="$HOME/.openclaw/miloco/.install-cache/${VERSION}/$wheel"
  if [ -f "$cached" ]; then
    info "从 Miloco 缓存复制 wheel"
    cp "$cached" "$dest"
    return
  fi

  # 同仓库 dist/ 或 miot-ctl/wheels/ 旁路构建产物
  local repo_dist
  for repo_dist in "$ROOT/../dist" "$ROOT/../../dist"; do
    if compgen -G "$repo_dist/miloco_miot-*.whl" >/dev/null; then
      info "从仓库 dist 复制 wheel"
      cp "$repo_dist"/miloco_miot-*.whl "$WHEEL_DIR/"
      return
    fi
  done

  # 在 xiaomi-miloco 仓库内可直接构建
  local backend_miot="$ROOT/../backend/miot"
  if [ -f "$backend_miot/pyproject.toml" ] && command -v uv >/dev/null 2>&1; then
    info "从源码构建 miloco-miot wheel..."
    (cd "$ROOT/../backend" && uv build --package miloco-miot -o "$WHEEL_DIR") || true
    if compgen -G "$WHEEL_DIR/miloco_miot-*.whl" >/dev/null; then
      return
    fi
  fi

  local urls=(
    "https://github.com/XiaoMi/xiaomi-miloco/releases/download/v${VERSION}/${wheel}"
    "https://gh-proxy.com/https://github.com/XiaoMi/xiaomi-miloco/releases/download/v${VERSION}/${wheel}"
    "https://gh-proxy.org/https://github.com/XiaoMi/xiaomi-miloco/releases/download/v${VERSION}/${wheel}"
  )
  info "下载 miloco-miot wheel (${PLATFORM})..."
  for url in "${urls[@]}"; do
    if curl -fL --retry 3 --connect-timeout 15 -o "$dest" "$url"; then
      info "下载完成: $dest"
      return
    fi
    rm -f "$dest"
  done
  fail "wheel 未找到。请手动放到 wheels/ 目录，或在 xiaomi-miloco 仓库内运行 install.sh"
}

main() {
  ensure_uv
  download_wheel

  if [ ! -d "$VENV" ]; then
    info "创建虚拟环境: $VENV"
    "$UV" venv "$VENV" --python 3.12 2>/dev/null || "$UV" venv "$VENV" --python 3.11
  fi

  PY="$VENV/bin/python"
  if [ -z "${UV_INDEX_URL:-}" ]; then
    export UV_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
  fi

  info "安装 Python 依赖..."
  "$UV" pip install --python "$PY" -r "$ROOT/requirements.txt"

  local wheel
  wheel="$(ls "$WHEEL_DIR"/miloco_miot-*.whl | head -1)"
  [ -n "$wheel" ] || fail "wheels/ 目录中没有 miloco_miot wheel"

  info "安装 miloco-miot SDK..."
  "$UV" pip install --python "$PY" --no-deps --force-reinstall "$wheel"

  chmod +x "$ROOT/miot-ctl"
  info "安装完成。运行: $ROOT/miot-ctl --help"
}

main "$@"
