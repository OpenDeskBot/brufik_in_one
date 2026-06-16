# shellcheck shell=bash
# 跨平台辅助（Linux / macOS / Windows Git Bash），供 start.sh 等脚本 source。

platform_is_windows() {
  case "$(uname -s 2>/dev/null)" in
    MINGW* | MSYS* | CYGWIN*) return 0 ;;
  esac
  case "${OS:-}" in
    Windows_NT) return 0 ;;
  esac
  return 1
}

# 输出 venv 内 Python 可执行文件路径；失败返回非 0。
platform_venv_python() {
  local root="${1:-.}"
  local candidates=(
    "$root/.venv/Scripts/python.exe"
    "$root/.venv/bin/python"
    "$root/.venv/bin/python3"
  )
  local c
  for c in "${candidates[@]}"; do
    if [[ -f "$c" ]]; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

# 检查解释器版本 >= major.minor
platform_python_version_ok() {
  local bin="$1"
  local major="$2"
  local minor="$3"
  "$bin" -c "import sys; exit(0 if sys.version_info >= (${major}, ${minor}) else 1)" 2>/dev/null
}

# 解析为 sys.executable 绝对路径（便于 Windows py launcher）
platform_resolve_python_executable() {
  local launcher="$1"
  if [[ "$launcher" == *" "* ]]; then
    eval "$launcher -c \"import sys; print(sys.executable)\"" 2>/dev/null
  else
    "$launcher" -c "import sys; print(sys.executable)" 2>/dev/null
  fi
}

# 查找满足版本的 Python；输出绝对路径。失败返回 1。
platform_find_python() {
  local req_mm="${1:-3.11}"
  local major="${req_mm%%.*}"
  local minor="${req_mm#*.}"
  minor="${minor%%.*}"

  local launchers=()
  if platform_is_windows; then
    launchers=("py -${major}.${minor}" "py -${major}" "py -3" "python" "python3" "python${major}" "python${major}.${minor}")
  else
    launchers=("python${major}.${minor}" "python${major}" "python3" "python")
  fi

  local launcher exe
  for launcher in "${launchers[@]}"; do
    exe="$(platform_resolve_python_executable "$launcher")" || continue
    if platform_python_version_ok "$exe" "$major" "$minor"; then
      echo "$exe"
      return 0
    fi
  done
  return 1
}

# 检测 libGLESv2（MediaPipe camera_frame 人脸检测在 Linux 上需要）
platform_has_libgles() {
  if platform_is_windows || [[ "$(uname -s 2>/dev/null)" == "Darwin" ]]; then
    return 0
  fi
  if ldconfig -p 2>/dev/null | grep -q 'libGLESv2\.so\.2'; then
    return 0
  fi
  local p
  for p in \
    /usr/lib64/libGLESv2.so.2 \
    /usr/lib/x86_64-linux-gnu/libGLESv2.so.2 \
    /usr/lib/libGLESv2.so.2; do
    if [[ -f "$p" || -L "$p" ]]; then
      return 0
    fi
  done
  return 1
}

platform_libgles_install_hint() {
  if command -v apt-get >/dev/null 2>&1 || command -v apt >/dev/null 2>&1; then
    echo "Debian/Ubuntu: sudo apt install -y libgles2-mesa libegl1-mesa"
  elif command -v dnf >/dev/null 2>&1; then
    echo "RHEL 8+: sudo dnf install -y mesa-libGLES mesa-libEGL"
  elif command -v yum >/dev/null 2>&1; then
    echo "CentOS 7: sudo yum install -y mesa-libGLES mesa-libEGL"
  else
    echo "请安装 mesa 提供的 libGLESv2.so.2（详见 README 人脸检测依赖）"
  fi
}

# 仅检查、不安装系统包；缺依赖时打印警告。
platform_warn_system_deps() {
  local missing=()
  command -v ffmpeg >/dev/null 2>&1 || missing+=("ffmpeg")
  if [[ ${#missing[@]} -gt 0 ]]; then
    echo "[warn] 未检测到: ${missing[*]}" >&2
    if platform_is_windows; then
      echo "[warn] Windows: winget install ffmpeg" >&2
    elif command -v apt-get >/dev/null 2>&1 || command -v apt >/dev/null 2>&1; then
      echo "[warn] Debian/Ubuntu: sudo apt update && sudo apt install -y ffmpeg" >&2
    elif command -v dnf >/dev/null 2>&1; then
      echo "[warn] RHEL 8+: sudo dnf install -y epel-release && sudo dnf install -y ffmpeg" >&2
    elif command -v yum >/dev/null 2>&1; then
      echo "[warn] CentOS 7: sudo yum install -y epel-release && sudo yum install -y ffmpeg" >&2
    else
      echo "[warn] 请安装 ffmpeg（详见仓库 README「ffmpeg」）。" >&2
    fi
    echo "[warn] 缺少 ffmpeg 时 opus 转码可能失败；可设 SKIP_SYSTEM_CHECK=1 跳过此检查。" >&2
  fi

  if ! platform_has_libgles; then
    echo "[warn] 未检测到 libGLESv2.so.2（camera_frame 人脸检测/MediaPipe 需要）。" >&2
    echo "[warn] $(platform_libgles_install_hint)" >&2
    echo "[warn] 未安装时人脸推理会失败；语音 /asr_chat 不受影响。" >&2
  fi
  return 0
}

# Git Bash 下 wait_tcp：优先 /dev/tcp，失败则用 python 探测。
platform_wait_tcp() {
  local host="$1"
  local port="$2"
  local max_wait="${3:-120}"
  local i

  for ((i = 0; i < max_wait; i++)); do
    if (echo >/dev/tcp/"$host"/"$port") 2>/dev/null; then
      return 0
    fi
    sleep 1
  done

  local py
  py="$(command -v python 2>/dev/null || command -v python3 2>/dev/null || true)"
  if [[ -z "$py" ]]; then
    return 1
  fi
  for ((i = 0; i < max_wait; i++)); do
    if "$py" -c "import socket; s=socket.create_connection(('$host', $port), 1); s.close()" 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

platform_run_sh() {
  local script="$1"
  shift
  if [[ -x "$script" ]]; then
    "$script" "$@"
  else
    bash "$script" "$@"
  fi
}

# 安装 CPU 版 torch/torchaudio（Windows / Linux 通用，不走 apt）
platform_install_cpu_torch() {
  local py="$1"
  local req_mm="${2:-3.11}"
  local torch_wheel_dir="${3:-$HOME/.cache/torch-wheels}"

  if "$py" -c "import importlib.metadata as m; assert m.version('torch').startswith('2.2.2') and m.version('torchaudio').startswith('2.2.2')" 2>/dev/null; then
    echo "检测到 torch/torchaudio 已安装，跳过 CPU 版安装。"
    return 0
  fi

  echo "安装 CPU 版 torch/torchaudio（避免 nvidia-cuda-*）..."
  local pip_extra=(--default-timeout=1000 --cache-dir="${HOME}/.cache/pip")
  if [[ -n "${PIP_INDEX_URL:-}" ]]; then
    pip_extra+=(--extra-index-url "$PIP_INDEX_URL")
  fi

  if platform_is_windows; then
    "$py" -m pip install torch==2.2.2 torchaudio==2.2.2 \
      --index-url https://download.pytorch.org/whl/cpu \
      "${pip_extra[@]}" || {
      echo "torch 安装失败：请确认 Python >= ${req_mm}。" >&2
      return 1
    }
    return 0
  fi

  local cp_tag torch_wheel_name torch_wheel_path torch_wheel_url
  cp_tag="$("$py" -c "import sys; v=sys.version_info; print('cp{0}{1}-cp{0}{1}'.format(v.major, v.minor))")"
  torch_wheel_name="torch-2.2.2+cpu-${cp_tag}-linux_x86_64.whl"
  # S3/CloudFront 对 URL 中字面量 '+' 返回 403，须编码为 %2B（aria2/wget 直链）
  torch_wheel_url="https://download.pytorch.org/whl/cpu/${torch_wheel_name//+/%2B}"
  torch_wheel_path="$torch_wheel_dir/$torch_wheel_name"
  mkdir -p "$torch_wheel_dir"

  _install_torch_pip_cpu() {
    local index
    for index in \
      "https://download.pytorch.org/whl/cpu" \
      "https://mirrors.aliyun.com/pytorch-wheels/cpu" \
      "https://mirrors.tencent.com/pytorch-wheels/cpu"; do
      echo "pip 安装 torch/torchaudio（index=${index}）..."
      if "$py" -m pip install torch==2.2.2+cpu torchaudio==2.2.2+cpu \
        --index-url "$index" \
        "${pip_extra[@]}"; then
        return 0
      fi
      echo "  该源失败，尝试下一镜像..."
    done
    return 1
  }

  if [[ -f "$torch_wheel_path" ]]; then
    echo "检测到本地 torch wheel: $torch_wheel_path"
    "$py" -m pip install "$torch_wheel_path" "${pip_extra[@]}" || return 1
  elif [[ "${TORCH_USE_ARIA2:-0}" == "1" ]] && command -v aria2c >/dev/null 2>&1; then
    echo "使用 aria2 下载 torch wheel（URL 已编码 + → %2B）..."
    if aria2c -x 16 -s 16 -k 1M -d "$torch_wheel_dir" -o "$torch_wheel_name" "$torch_wheel_url" \
      && [[ -f "$torch_wheel_path" ]]; then
      "$py" -m pip install "$torch_wheel_path" "${pip_extra[@]}" || return 1
    else
      echo "aria2 下载失败，改用 pip..." >&2
      rm -f "$torch_wheel_path"
      _install_torch_pip_cpu || {
        echo "torch 安装失败：请确认 Python >= ${req_mm} 且为 linux x86_64。" >&2
        return 1
      }
    fi
  else
    _install_torch_pip_cpu || {
      echo "torch 安装失败：可设 TORCH_USE_ARIA2=1 重试直链下载，或手动安装 torch==2.2.2+cpu。" >&2
      return 1
    }
  fi
}
