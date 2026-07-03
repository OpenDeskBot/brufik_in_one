# deskbot-server

主服务：VAD → FunASR → LLM（多轮 tools）→ TTS → pb 下行。环境搭建见 [../README.md](../README.md)。

## 启动

```bash
# 一键启动（含 Web 控制台）
./start.sh
SKIP_SETUP=1 ./start.sh
```

```bash
cp .env.example .env   # 必填 LLM_API_KEY
```

| 端口 | 服务 |
|------|------|
| **9000** | WebSocket + HTTP API（设备主链路） |
| **5050** | Flask Web 控制台（`DESKBOT_START_WEB=1`，`start.sh` 默认开启） |

---

## Web 控制台（`:5050`）

邮箱注册 / 登录后可用：

| 路径 | 说明 |
|------|------|
| `/app` | 工作台：用量概览 |
| `/app/devices` | 我的设备：绑定、切换 `device_id` |
| `/app/scheduled-tasks` | 定时任务：cron 任务列表（东八区），可删除 |
| `/app/memories` | 记忆：按设备增删改查长期记忆 |
| `/app/face-profiles` | 人脸识别：按设备查看档案 |
| `/app/usage` | 用量看板：按 Key / 设备统计 |
| `/app/settings` | 账号设置：API Key 管理 |
| `/debug/devices` | 调试：在线设备、流水线 |
| `/debug/llm` | 调试：LLM 试聊 |
| `/debug/tts` | 调试：豆包 TTS |
| `/debug/simulation` | 调试：pb 仿真 |

首次启动自动创建免费体验 API Key，写入 `data/.free_api_key`（每日 1GB 总字节配额）。

---

## WebSocket（`:9000`）

| 路径 | 说明 |
|------|------|
| `/asr_chat?device_id=&api_key=` | **生产设备**：语音 + 可选 `camera_frame`；pb 下行（**须 API Key**） |
| `/camera_view?device_id=&api_key=` | 调试：JPEG 预览 |
| `/device_pipeline?role=subscriber&device=&api_key=` | 调试：流水线事件 |

`device_id` 别名：`device` / `deviceid` / `id`。协议：[../docs/esp32_pb_protocol.md](../docs/esp32_pb_protocol.md)。

默认 **`asr_chat_device_pb_only: true`**：设备只收 `pb_*` + PCM。

---

## HTTP API（`:9000`）

| 路径 | 说明 |
|------|------|
| `/health` | 健康检查（免 Key） |
| `/api/devices` | 在线设备列表 |

本机 `127.0.0.1` 经 Flask 代理转发时可免 Key；**ESP32 直连须带 API Key**。

---

## LLM 与工具

对话采用 **JSON 回复 + `tools` 数组**；有 tools 时服务端执行后再次调用 LLM（最多 8 轮），无 tools 时走 TTS / pb。

| 工具 | 说明 |
|------|------|
| `schedule_task` | cron 定时任务增删改查（北京时间）；用户说「N 分钟后提醒我…」时**必须**调用，禁止口头答应 |
| `set_camera_follow` | 人脸舵机跟随 |
| `capture_camera` | 获取最近相机帧 |
| `register_face` | 注册 / 更新人脸档案 |
| `memory_add` / `memory_delete` | 长期记忆 |
| `session` | 查询对话 session |
| `webfetch` / `websearch` | 联网抓取 / 搜索 |
| `read` / `write` | 读写 `data/device/{device_id}/tmp/` |

定时任务由后台调度器每分钟轮询，到期后复用创建时的 `session_id` 作为 LLM 上下文并播报提醒。

人设与工具说明：`data/llm_system.txt`（全局模板）及 `data/device/{device_id}/llm_system.txt`（设备级，优先）。

---

## 设备数据（按 `device_id` 隔离）

运行时数据在 `data/device/{device_id}/`（**不入 Git**），首次使用设备时从 `data/` 复制模板：

| 文件 / 目录 | 说明 |
|-------------|------|
| `llm_system.txt` | 设备 LLM 人设 |
| `user_memory.json` | 长期记忆 |
| `face_profiles.json` | 人脸档案 |
| `session/` | 对话 session（10 分钟无对话开新 session） |
| `tmp/` | LLM `read` / `write` 沙箱目录 |

全局 SQLite：`data/opendesk.db`（用户、API Key、设备绑定、`scheduled_tasks` 表）。

---

## 配置

- **`.env`**：`LLM_API_KEY`、`DOUBAO_TTS_*`（豆包 TTS）、`ASR_MODEL_DIR`、`DESKBOT_WEB_PUBLIC_HOST`（多网卡时填局域网 IP）、`DESKBOT_WEB_SECRET_KEY`（生产必设）
- **`config.yaml`**：`audio.input_codec`、`llm.model_name`、`tts.provider`（`doubao`）、`server.asr_chat_device_pb_only`、`debug.asr_auto_reply`

架构概要：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

---

## 联调

```bash
source .venv/bin/activate
python tools/test_client.py \
  --ws-url "ws://127.0.0.1:9000/asr_chat?device_id=deskbot_dev&api_key=<key>" \
  --input-wav demo_16k_mono.wav
```

更多脚本见 [tools/README.md](tools/README.md)。

```bash
source .venv/bin/activate
pytest tests/ -q
```
