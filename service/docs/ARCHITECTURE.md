# deskbot-server 架构（概要）

分层：**ws / web**（协议入口）→ **application**（用例）→ **core**（端口 Protocol）← **infrastructure**（ASR / LLM / TTS 实现）。**pb**、**vision** 不依赖 ws。

## 主链路（语音对话）

```
/asr_chat
  → VAD + FunASR（ChatService.asr）
  → run_chat_turn
      → ensure_active_session（按 device 加载历史）
      → complete_llm_with_tool_loop（多轮 tools，最多 8 轮）
      → TTS phoneme 分片
      → pb.wire 组包
      → DownlinkPort.send_pb_wire
```

摄像头：`camera_frame` + JPEG → `camera_jpeg_pipeline` → 调试订阅 / 人脸跟踪 / 舵机跟随。

## 定时任务

```
ScheduledTaskScheduler（60s 轮询）
  → claim_due_tasks（SQLite scheduled_tasks，东八区 cron）
  → run_chat_turn（reuse_session_id，强制 TTS 提醒）
  → finish_scheduled_task
```

任务由 LLM `schedule_task` 工具创建；**不由服务端从用户话术正则推断**。

## 模块一览

| 目录 / 模块 | 职责 |
|-------------|------|
| `ws/` | WebSocket 路由、`AsrChatHub`、pb 下行队列、API Key 门禁 |
| `web/` | Flask 控制台（`app_bp` 设备/任务/记忆/人脸，`debug_bp` 调试） |
| `auth/` | 用户注册登录、API Key、设备绑定 |
| `db/` | SQLAlchemy 模型（User、ApiKey、Device、ScheduledTask、UsageDaily） |
| `application/chat_flow.py` | 单轮对话编排（LLM + TTS + pb） |
| `application/llm_tool_loop.py` | LLM 多轮 tool-call |
| `application/llm_tool_runner.py` | 执行 `tools` 指令 |
| `application/scheduled_task_scheduler.py` | 到期任务调度 |
| `scheduled_task_service.py` | cron CRUD、claim / finish |
| `session_store.py` | `data/device/{id}/session/*.json` |
| `device_data.py` | 按设备路径解析配置模板 |
| `memory_store.py` | 长期记忆（全局或设备级 JSON） |
| `pb/` | 表情、口型、舵机、wire 组包 |
| `vision/` | 人脸检测、embedding、跟随 |

## WebSocket

| 路径 | 生产必需 |
|------|----------|
| `/asr_chat?device_id=&api_key=` | 是 |
| `/camera_view`、`/device_pipeline` | 否（调试） |

单进程 asyncio；`ChatService`（含 FunASR）全进程共享。重 CPU 走 `asyncio.to_thread`；`config.yaml` 的 `max_concurrent_asr` / `max_concurrent_face_infer` 限流。

## 装配（`main.py`）

```
init_database()
build_chat_service()
AsrChatHub + ScheduledTaskScheduler.start()
websockets.serve(:9000)
```

Web 控制台独立进程：`python -m deskbot_server.web`（`:5050`，`start.sh` 默认拉起）。

## 测试

```bash
source .venv/bin/activate
pytest tests/ -q
```
