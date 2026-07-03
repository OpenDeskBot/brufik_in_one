# opendesk-service

ESP32 桌面机器人后端：设备上传语音（可选摄像头），服务端完成 **VAD → ASR → LLM → TTS**，经 **pb** 协议下发 PCM、屏幕动画与舵机指令。

**License:** [GPL-3.0](LICENSE)

---

## 最快部署（3 步）

**环境：** Ubuntu 22.04 / 24.04（或 macOS / Windows Git Bash）、Python **3.11**、`ffmpeg`、可访问外网（首次下载模型与 pip 包）。

```bash
# 1. 系统依赖（Ubuntu）
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev ffmpeg curl git

# 2. 配置大模型 Key（必填）
cd opendesk-service
cp .env.example .env
# 编辑 .env，填写 LLM_API_KEY=sk-...

# 3. 一键启动（自动建 venv、下载模型、起主服务 + Web 控制台）
chmod +x start.sh
./start.sh
```

首次启动约需数分钟（ASR 模型约 900MB）。**第二次及以后**若 `.venv` 已完整，直接 `./start.sh` 会自动跳过 pip 安装；也可显式：

```bash
SKIP_SETUP=1 ./start.sh
# 或
FAST_START=1 ./start.sh
```

| 服务 | 地址 | 用途 |
|------|------|------|
| **Web 控制台** | `http://<本机IP>:5050/` | 注册登录、设备与 API Key、定时任务、记忆等 |
| **设备 WebSocket** | `ws://<本机IP>:9000/asr_chat?device_id=<id>&api_key=<key>` | ESP32 语音对话主链路 |

启动后终端会打印**免费体验 API Key** 路径：`data/.free_api_key`（前缀 `odk_free_`，每日 1GB 配额）。

---

## 快速使用

### 1. 打开控制台

浏览器访问 `http://<本机IP>:5050/` → **注册**账号 → 登录进入工作台。

### 2. 获取 API Key

**账号设置** 中创建 API Key，或直接使用首次启动生成的 `data/.free_api_key`。

### 3. 绑定设备

**我的设备** 中添加 `device_id`（与固件一致，如 `deskbot_e8f60a8cf9b0`）。后续管理定时任务、记忆、人脸档案时需先**选择设备**。

### 4. 连接设备对话

固件连接：

```
ws://<本机IP>:9000/asr_chat?device_id=<你的device_id>&api_key=<你的key>
```

说话后发送 `flush`，服务端识别 → 调用 LLM → TTS → pb 下行播放。

### 5. 无硬件时本地试跑

```bash
source .venv/bin/activate
python tools/test_client.py \
  --ws-url "ws://127.0.0.1:9000/asr_chat?device_id=deskbot_dev&api_key=<key>" \
  --input-wav demo_16k_mono.wav
```

WAV 须 **16 kHz / mono / s16le**。麦克风实时测试见 [tools/README.md](tools/README.md)。

### 6. 控制台常用功能

| 菜单 | 说明 |
|------|------|
| 工作台 | 用量概览与快捷入口 |
| 我的设备 | 绑定 / 切换 `device_id` |
| 定时任务 | 查看 LLM 创建的 cron 任务（北京时间），可删除 |
| 记忆 | 按设备管理长期记忆（注入 LLM） |
| 人脸识别 | 按设备查看人脸档案 |
| 用量看板 | 按 Key / 设备查看 ASR、人脸、LLM、TTS 字节统计 |
| 调试台 | 设备在线、LLM 试聊、豆包 TTS、流水线等 |

语音对话中，LLM 可通过工具创建定时提醒（`schedule_task`）、读写设备临时文件、联网搜索等，详见 [docs/SERVER.md](docs/SERVER.md)。

---

## 与 ESP32 的交互（概要）

单条 WebSocket：`/asr_chat?device_id=<id>&api_key=<key>`（也可用 Header `X-API-Key`）。上行、下行均采用 **「JSON + 紧随一条 binary」**，长度由 **`next_bin_len`** 声明，**不用 base64**。

```
┌──────── ESP32 ────────┐                    ┌──── deskbot-server ────┐
│ 麦克风 Opus/PCM       │  audio + binary    │ VAD → FunASR → 文本     │
│ 可选 JPEG             │  camera_frame      │ → DashScope LLM + tools │
│ flush / pb_ack        │ ─────────────────► │ → 豆包 TTS + 音素口型   │
│                       │                    │ → 组 pb + PCM           │
│ 播放 PCM + 画屏       │  pb_* + binary     │                         │
│ 舵机 / 音量 / 帧率    │ ◄───────────────── │                         │
└───────────────────────┘                    └─────────────────────────┘
```

TTS 使用火山引擎豆包（`tts.provider: doubao`），凭证配置见 `.env` 与调试台「TTS 调试」。

### 上行（设备 → 服务端）

| JSON `type` | binary | 说明 |
|-------------|--------|------|
| `audio` + `next_bin_len` | Opus 或 PCM | 语音流；段结束发 `flush` 触发识别与回复 |
| `camera_frame` + `next_bin_len` | JPEG | 可选；人脸检测与调试预览 |
| `pb_ack` | 无 | 播放缓冲回压 |
| `ping` | 无 | 保活 |

### 下行（服务端 → 设备）

默认 **`asr_chat_device_pb_only: true`**：只处理 **`pb_start` / `pb_chunk` / `pb_end` / `pb_single`**，以及紧随的 **s16le PCM**（24 kHz mono）。

完整字段见 **[docs/esp32_pb_protocol.md](docs/esp32_pb_protocol.md)**。

### 固件要点

1. URL 必须带稳定 **`device_id`** 与有效 **`api_key`**。
2. JSON 与 binary **严格成对、顺序发送**。
3. 周期性 **`pb_ack`** 做播放回压。

---

## 数据与目录

| 路径 | 说明 |
|------|------|
| `data/opendesk.db` | 用户、API Key、设备绑定、定时任务（SQLite） |
| `data/.free_api_key` | 免费体验 Key（勿提交 Git） |
| `data/device/{device_id}/` | 按设备隔离的配置、session、记忆等（**不入 Git**） |
| `data/llm_system.txt` | 全局 LLM 人设模板（新设备首次使用时复制） |

---

## 文档索引

| 文档 | 内容 |
|------|------|
| [docs/api_interfaces.md](docs/api_interfaces.md) | Web 控制台、deskbot 设备服务、TTS 侧车接口清单 |
| [docs/esp32_pb_protocol.md](docs/esp32_pb_protocol.md) | ESP32 通信、鉴权、pb 协议 |
| [docs/SERVER.md](docs/SERVER.md) | 主服务 API、配置、LLM 工具 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 代码分层与模块 |
| [tools/README.md](tools/README.md) | 本地联调脚本 |
| [docs/README.md](docs/README.md) | 文档目录 |

[CONTRIBUTING.md](CONTRIBUTING.md) · [SECURITY.md](SECURITY.md)
