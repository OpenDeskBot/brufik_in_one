# 接口清单

更新时间：2026-07-01

本文按当前代码实现整理所有对外接口，覆盖 Web 控制台、deskbot 设备服务、paddlespeech TTS 侧车。重点列 HTTP / WebSocket 路由、鉴权、主要参数与返回用途；ESP32 pb 字段细节见 [esp32_pb_protocol.md](./esp32_pb_protocol.md)。

## 服务与端口

| 服务 | 默认地址 | 实现位置 | 说明 |
|------|----------|----------|------|
| Web 控制台 | `http://<host>:5050` | `deskbot_server.web` | Flask 页面、用户登录、设备/记忆/模型管理、调试页、代理到设备服务 |
| deskbot 设备服务 | `ws://<host>:9000` / `http://<host>:9000` | `deskbot_server.ws` | ESP32 主链路、调试订阅、设备下发、轻量 HTTP API |
| paddlespeech TTS 侧车 | `ws://<host>:8092` | `paddlespeech_server` | 官方流式 TTS 与音素对齐 TTS |

## 鉴权约定

### Web 控制台 `:5050`

| 范围 | 鉴权 |
|------|------|
| `/`、`/login`、`/register`、`/health`、`/static/*` | 公开 |
| 其它页面 | 需要 Flask 登录态；未登录重定向到 `/login` |
| `/api/*` | 需要 Flask 登录态；未登录返回 `401 {"ok": false, "error": "unauthorized"}` |
| `/proxy/deskbot/*` | 需要登录态，并按当前用户绑定设备过滤/补全 `device_id` |

### deskbot 设备服务 `:9000`

| 范围 | 鉴权 |
|------|------|
| `GET /health` | 公开 |
| `/api/*` | 需要 API Key；支持查询参数 `api_key` / `apikey` / `key`，Header `X-API-Key`，或 `Authorization: Bearer <key>` |
| `/asr_chat` | 需要 API Key |
| `/device_pipeline` 生产者 | 需要 API Key |
| `/device_pipeline` 订阅者、`/camera_view` | API Key 或 Web 控制台签发的 `debug_token` |

常见错误：无 Key 为 `401` 或 WS close `1008 api_key_required`；免费 Key 超额为 `429` 或 WS close `1008 quota_exhausted`；用户 Key 操作未绑定设备为 `403 forbidden_device`。

## Web 控制台页面与表单 `:5050`

### 公开与账号

| 方法 | 路径 | 用途 | 主要输入 |
|------|------|------|----------|
| GET | `/` | 站点首页；已登录时跳转 `/home` | - |
| GET | `/login` | 登录页 | `next` query |
| POST | `/login` | 登录提交 | form: `email`, `password`, `next` |
| GET | `/register` | 注册页 | - |
| POST | `/register` | 注册提交并登录 | form: `email`, `password`, `confirm_password` |
| POST | `/logout` | 退出登录 | - |
| GET | `/health` | Web 控制台健康检查 | 返回纯文本 `ok` |

### 2C 用户页面

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/home` | 2C 首页 |
| GET | `/voice` | 语音设置/入口 |
| GET | `/expr` | 表情设置/入口 |
| GET | `/my/memories` | 我的记忆 |
| GET | `/my/reminders` | 我的提醒 |
| GET | `/my/people` | 我的人物/人脸 |
| GET | `/my/devices` | 我的设备 |
| GET | `/advanced` | 高级设置 |

### 管理后台页面

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/app/` | 工作台 |
| GET | `/app/devices` | 设备管理 |
| GET | `/app/scheduled-tasks` | 定时任务列表 |
| GET | `/app/face-profiles` | 人脸档案 |
| GET | `/app/memories` | 长期记忆 |
| GET | `/app/llm-models` | LLM 模型配置 |
| GET | `/app/usage` | 用量看板 |
| GET | `/app/settings` | 账号设置/API Key |
| POST | `/app/settings/profile` | 更新显示名 | form: `display_name` |
| POST | `/app/settings/password` | 修改密码 | form: `old_password`, `new_password`, `confirm_password` |
| POST | `/app/settings/api-keys` | 创建 API Key | form: `key_name` |
| POST | `/app/settings/api-keys/{key_id}/revoke` | 吊销 API Key | path: `key_id` |

### 调试页面

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/debug/devices` | 在线设备、流水线、表情/舵机调试 |
| GET | `/debug/tts` | 豆包 TTS 调试 |
| GET | `/debug/llm` | LLM 试聊与 system prompt |
| GET | `/debug/paddlespeech` | PaddleSpeech 音素 TTS 调试 |
| GET | `/debug/simulation` | 模拟对话/显示调试 |

## Web 控制台 JSON API `:5050`

### 2C 高级设置

| 方法 | 路径 | 用途 | 主要输入 |
|------|------|------|----------|
| GET | `/api/advanced` | 汇总当前用户、设备、用量、API Key、LLM 配置 | - |
| PATCH | `/api/advanced/profile` | 更新显示名 | JSON: `display_name` |
| POST | `/api/advanced/password` | 修改密码 | JSON: `old_password`, `new_password`, `confirm_password` |
| POST | `/api/advanced/api-keys` | 创建 API Key | JSON: `name` |
| DELETE | `/api/advanced/api-keys/{key_id}` | 吊销 API Key | path: `key_id` |
| GET | `/api/emotion_expr_map` | 读取情绪到表情映射 | query: `device_id` 可选，默认当前设备 |
| POST | `/api/emotion_expr_map` | 保存情绪到表情映射 | JSON: `map` |

### 管理后台数据 API

| 方法 | 路径 | 用途 | 主要输入 |
|------|------|------|----------|
| GET | `/app/api/devices` | 当前用户绑定设备列表 | - |
| POST | `/app/api/devices` | 绑定设备并设为当前设备 | JSON/form: `device_id`, JSON: `display_name` 可选 |
| POST | `/app/api/devices/select` | 切换当前设备；空 `device_id` 清空选择 | JSON: `device_id` |
| DELETE | `/app/api/devices/{device_id}` | 解绑设备 | path: `device_id` |
| GET | `/app/api/scheduled-tasks` | 查询设备定时任务 | query: `device_id` 可选，默认当前设备 |
| DELETE | `/app/api/scheduled-tasks/{task_id}` | 删除定时任务 | path: `task_id`, query: `device_id` 可选 |
| GET | `/app/api/face-profiles` | 查询人脸档案摘要 | query: `device_id` 可选 |
| PUT/PATCH | `/app/api/face-profiles/{person_id}` | 更新人脸名称 | JSON: `name`, query: `device_id` 可选 |
| DELETE | `/app/api/face-profiles/{person_id}` | 删除人脸档案 | path: `person_id`, query: `device_id` 可选 |
| GET | `/app/api/memories` | 查询长期记忆 | query: `device_id` 可选 |
| POST | `/app/api/memories` | 新增长期记忆 | JSON/form: `text` |
| GET | `/app/api/memories/{entry_id}` | 查询单条记忆 | path: `entry_id` |
| PUT/PATCH | `/app/api/memories/{entry_id}` | 更新记忆文本 | JSON: `text` |
| DELETE | `/app/api/memories/{entry_id}` | 删除记忆 | path: `entry_id` |
| GET | `/app/api/llm-models` | 查询设备 LLM 模型列表与当前生效配置 | query: `device_id` 可选 |
| POST | `/app/api/llm-models` | 新增设备 LLM 模型 | JSON: `name`, `model_name`, `protocol`, `base_url`, `api_key` |
| PUT | `/app/api/llm-models/{model_id}` | 更新设备 LLM 模型 | JSON: `name`, `model_name`, `protocol`, `base_url`, `api_key` 可选 |
| DELETE | `/app/api/llm-models/{model_id}` | 删除设备 LLM 模型 | path: `model_id` |
| POST | `/app/api/llm-models/select` | 选择/清空当前 LLM 模型 | JSON: `model_id`，可为 `null` |

这些接口均会校验当前用户是否拥有目标 `device_id`。未选设备返回 `400`，设备不属于当前用户返回 `403`。

### Web 调试 API

| 方法 | 路径 | 用途 | 主要输入 |
|------|------|------|----------|
| GET | `/api/debug/ws_token` | 为已登录用户签发调试 WebSocket `debug_token` | - |
| GET | `/api/doubao_tts/speakers` | 豆包 TTS 说话人预设 | - |
| GET | `/api/doubao_tts/config` | 读取豆包 TTS 配置，密钥脱敏 | - |
| POST | `/api/doubao_tts/config` | 保存豆包 TTS 配置 | JSON: `api_key`, `speaker`, `resource_id`, `model`, `ws_url`, `sample_rate`, `audio_format` |
| POST | `/api/doubao_tts/synthesize` | 豆包 TTS 合成并返回 WAV base64 | JSON: `text`，可带临时 TTS 配置 |
| POST | `/api/paddlespeech/phoneme_tts` | 代理调用音素 TTS，返回分片与 WAV base64 | JSON: `text`, `spk_id` |
| GET | `/api/llm/system_prompt` | 读取设备 LLM system prompt | query/body/session: `device_id` |
| POST | `/api/llm/system_prompt` | 保存设备 LLM system prompt | JSON: `system_prompt` 或 `content`, `device_id` 可选 |
| POST | `/api/llm/chat` | 调试 LLM 对话，不直接走设备 ASR | JSON: `text`, `history`, `system_prompt`, `temperature`, `device_id`, `device_context` |
| GET | `/api/servo_config` | 读取舵机配置 | query/session: `device_id` |
| POST | `/api/servo_config` | 保存舵机配置 | JSON: servo 文档 |
| GET | `/api/camera_face_config` | 读取相机人脸检测配置 | query/session: `device_id` |
| POST | `/api/camera_face_config` | 保存相机人脸检测配置 | JSON: camera face 文档 |
| GET | `/api/face_profiles` | 读取人脸档案原始文件 | query/session: `device_id` |
| POST | `/api/face_profiles/register` | 绑定当前检测到的人脸为档案 | JSON: `device_id`, `face_id`, `name` |
| GET | `/api/user_memory` | 读取设备长期记忆原始列表 | query/session: `device_id` |
| POST | `/api/user_memory` | 新增长期记忆 | JSON: `text`, `device_id` 可选 |
| DELETE | `/api/user_memory/{entry_id}` | 删除长期记忆 | path: `entry_id`, query/session: `device_id` |
| GET | `/api/face_mouth_by_phoneme` | 读取音素口型组表 | query/session: `device_id` |
| POST | `/api/face_mouth_by_phoneme` | 保存音素口型组表 | JSON: 组表数组或文档 |
| GET | `/api/face_expr_scenes` | 读取表情场景配置 | query/session: `device_id` |
| POST | `/api/face_expr_scenes` | 保存表情场景配置 | JSON: 场景数组 |
| GET | `/api/scene_playbooks` | 读取场景 playbook 配置 | query/session: `device_id` |
| POST | `/api/scene_playbooks` | 保存场景 playbook 配置 | JSON: playbook 数组 |
| GET | `/api/health` | 检查 deskbot-server 与 TTS 端口是否可连 | - |

### 代理 API

| 方法 | 路径 | 用途 |
|------|------|------|
| GET/POST/PUT/DELETE/OPTIONS | `/proxy/deskbot/{subpath}` | 登录用户访问 `:9000` HTTP API 的代理入口；会过滤 `/api/devices`，并对需要设备的路径校验/补全当前 `device_id` |

代理会将请求转发到 `deskbot_upstream_base()`，并尽量附带服务器保存的免费 API Key。当前设备相关的代理路径包括 `/api/device_servo`、`/api/device_tts`、`/api/device_pb_scene`、`/api/device_pb_anim`、`/api/device_pb_expr_scene`、`/api/device_pb_scenes`、`/api/scene_playbook/run`。

## deskbot WebSocket `:9000`

### `/asr_chat`

生产设备主链路。默认 URL：

```text
ws://<host>:9000/asr_chat?device_id=<id>&api_key=<key>
```

`device_id` 别名：`device_id`、`deviceid`、`device`、`id`。连接成功后服务端发送：

```json
{"type": "ready", "device_id": "<id>"}
```

设备上行消息：

| 方向 | 消息 | binary | 用途 |
|------|------|--------|------|
| 设备 -> 服务端 | `{"type":"audio","codec":"opus|pcm16","next_bin_len":N}` | 下一帧为音频 | 输入语音；也兼容 `data` base64 或裸 binary |
| 设备 -> 服务端 | `{"type":"flush"}` | 无 | 结束当前语音段，触发 ASR/LLM/TTS |
| 设备 -> 服务端 | `{"type":"camera_frame","codec":"jpeg","next_bin_len":N,"seq":...}` | 下一帧为 JPEG | 上传画面用于人脸检测、预览、跟随 |
| 设备 -> 服务端 | `{"type":"pb_ack", ...}` | 无 | 播放回压与状态上报；会进入设备流水线订阅 |
| 设备 -> 服务端 | `{"type":"user_text","text":"..."}` | 无 | 调试：跳过 ASR，直接进入对话 |
| 设备 -> 服务端 | `{"type":"ping"}` | 无 | 保活；非 pb-only 模式回 `pong` |

服务端下行消息：

| 消息 | binary | 用途 |
|------|--------|------|
| `pb_start` / `pb_chunk` / `pb_end` | 可选，取决于 `audio.next_bin_len` | 多片 pb 下发，含 PCM、表情、舵机 |
| `pb_single` | 可选 | 单片 pb 下发 |
| `face_info` | 无 | 仅 `send_face_info_to_asr_chat=true` 且非 pb-only 时可能下发 |
| `ready` / `pong` / 错误类 JSON | 无 | 握手、保活、异常提示 |

默认 `asr_chat_device_pb_only: true`，设备端应只依赖 `pb_*` + PCM。pb wire 规则见 [esp32_pb_protocol.md](./esp32_pb_protocol.md)。

### `/camera_view`

调试 JPEG 预览订阅。URL：

```text
ws://<host>:9000/camera_view?device_id=<id>&api_key=<key>
ws://<host>:9000/camera_view?device_id=<id>&debug_token=<token>
```

| 方向 | 消息 | 用途 |
|------|------|------|
| 服务端 -> 客户端 | `{"type":"ready","channel":"camera_view","device_filter":"...","expects":"binary JPEG frames preceded by camera_frame meta"}` | 订阅就绪 |
| 服务端 -> 客户端 | `{"type":"camera_frame", ...}` + 下一帧 JPEG binary | 最新相机帧与检测元数据 |
| 客户端 -> 服务端 | `{"type":"ping"}` | 服务端回 `{"type":"pong"}` |

### `/device_pipeline`

流水线事件通道，支持生产者与订阅者两种角色。

生产者 URL：

```text
ws://<host>:9000/device_pipeline?device_id=<id>&api_key=<key>
```

订阅者 URL：

```text
ws://<host>:9000/device_pipeline?role=subscriber&device_id=<id>&api_key=<key>
ws://<host>:9000/device_pipeline?role=subscriber&device_id=<id>&debug_token=<token>
```

订阅者 `role` 支持 `subscriber`、`sub`、`viewer`、`consumer`；`device_id` 可省略，省略时订阅全部设备。

| 角色 | 方向 | 消息 | 用途 |
|------|------|------|------|
| 全部 | 服务端 -> 客户端 | `{"type":"ready","channel":"device_pipeline",...}` | 通道就绪 |
| 订阅者 | 服务端 -> 客户端 | `{"type":"pipeline_snapshot","items":[...],"device_filter":...,"max_events":100}` | 连接时快照 |
| 订阅者 | 服务端 -> 客户端 | `{"type":"pipeline_event","event":{...}}` | 一轮 ASR/LLM/TTS 或自动下发事件 |
| 订阅者 | 服务端 -> 客户端 | `{"type":"pipeline_stage","event":{...}}` | 阶段事件，例如 `pb_ack`、`face_pos` |
| 生产者 | 客户端 -> 服务端 | 任意 JSON 事件，含 `asr_text`、`llm_text`、`tts_text`、耗时、状态等 | 归一化后写入流水线窗口 |
| 生产者 | 服务端 -> 客户端 | `{"type":"pipeline_ack","seq":N}` | 事件已接收 |
| 生产者 | 服务端 -> 客户端 | `{"type":"pipeline_rejected","reason":"invalid_payload"}` | 事件无效 |
| 全部 | 客户端 -> 服务端 | `{"type":"ping"}` | 服务端回 `pong` |

### `/camera`

旧独立相机通道已移除。连接会收到错误消息并以 close code `1008` 关闭，应改用 `/asr_chat` 的 `camera_frame`。

## deskbot HTTP API `:9000`

所有 `/api/*` 均支持 `OPTIONS` CORS 预检。除 `/health` 外，均需 API Key。

| 方法 | 路径 | 用途 | 主要输入 |
|------|------|------|----------|
| GET | `/health` | 存活探针 | - |
| GET | `/api/devices` | 当前在线设备列表；用户 Key 会按绑定设备过滤 | - |
| GET | `/api/asr_auto_reply` | 查询/设置 ASR 自动应答开关 | query: `enabled=1|0|true|false` 可选 |
| GET | `/api/pb_idle_auto_dispatch` | 查询/设置空闲 pb 自动下发开关 | query: `enabled=1|0|true|false` 可选 |
| GET | `/api/camera_servo_auto_mode` | 查询/设置相机舵机自动模式 | query: `mode=follow|follow_frontal|gaze|off` 可选 |
| GET | `/api/debug_prefs` | 查询/批量设置调试偏好 | query: `asr_auto_reply`, `camera_servo_auto_mode` 可选 |
| GET | `/api/pipeline_recent` | 获取流水线事件滚动窗口 | query: `device_id` 可选，`limit` 默认最多 100 |
| GET | `/api/device_servo` | 向设备 `/asr_chat` 下发单片舵机 pb | query: `device_id`, `dyaw`, `dpitch`, `ms`, `xm`, `ym`, `action`, `level`, `with_scene`/`append_scene` |
| GET | `/api/device_pb_scenes` | 列出内置/配置的 pb 场景名 | - |
| GET/POST | `/api/device_tts` | 跳过 LLM，直接 TTS 并下发到设备 | query 或 JSON: `device_id`, `text`, `scene` 可选 |
| GET/POST | `/api/scene_playbook/run` | 执行场景 playbook，并与 TTS/表情/舵机交错下发 | GET: `device_id`, `name`; POST JSON: `device_id`, `playbook` 或 `name` |
| GET | `/api/device_pb_scene` | 按场景名向设备顺序下发 `pb_start -> pb_chunk* -> pb_end` | query: `device_id`, `scene` |
| GET/POST | `/api/servo_config` | 读取/写入舵机配置文件 | query/JSON: `device_id` 可选；POST body 为 servo 文档 |
| GET/POST | `/api/face_mouth_by_phoneme` | 读取/写入音素口型组表 | query/JSON: `device_id` 可选；POST body 为组表 |
| GET/POST | `/api/device_pb_anim` | 下发仅含 `anim` 的单片 `pb_single` | GET: `device_id`, `anim_b64`, `chunk_ms`, `action`, `level`; POST JSON: `device_id`, `anim`, `chunk_ms`, `action`, `level` |
| GET/POST | `/api/face_expr_scenes` | 读取/写入设计表情场景 | query/JSON: `device_id` 可选；POST body 为场景数组 |
| GET | `/api/device_pb_expr_scene` | 按设计表情场景名向设备下发 pb 链 | query: `device_id`, `scene` 或 `name` |

未知 `/api/*` 返回 `404 {"error":"not found","path":"..."}`。不支持的方法返回 `405`。

## paddlespeech WebSocket `:8092`

### `/paddlespeech/tts/streaming`

PaddleSpeech 官方流式 TTS WebSocket，由 `paddlespeech.server.ws.api.setup_router` 根据配置 `engine_list: ['tts_online-onnx']` 注册。本项目未修改该协议；deskbot 默认通过 `config.yaml -> tts.ws_url` 连接它。

### `/paddlespeech/tts/streaming_phoneme`

音素分片 TTS，供口型对齐使用。传输为 WebSocket JSON 文本帧。

请求流程：

| 步骤 | 客户端消息 | 服务端响应 |
|------|------------|------------|
| 开始 | `{"signal":"start"}` | `{"status":0,"signal":"server ready","session":"..."}` |
| 合成 | `{"text":"你好","spk_id":0}` | `{"status":1,"segments":[...]}`，随后 `{"status":2,"segments":[]}` |
| 结束 | `{"signal":"end","session":"..."}` | `{"status":0,"signal":"connection will be closed","session":"..."}` |

`segments[]` 字段：

| 字段 | 说明 |
|------|------|
| `phoneme_id` | PaddleSpeech 音素 ID |
| `phoneme` | 音素符号 |
| `ms` | 该分片估算时长，毫秒 |
| `audio` | 小端 int16 PCM 分片的 base64 |

错误响应：

```json
{"status": -1, "message": "...", "segments": []}
```

常见错误包括 `send signal start first`、`empty text`、`empty phone_ids for text`、`invalid request json`。

## 源码索引

| 范围 | 文件 |
|------|------|
| Flask app 注册与全局鉴权 | `service/deskbot-server/src/deskbot_server/web/app.py` |
| Web 控制台账号路由 | `service/deskbot-server/src/deskbot_server/web/blueprints/auth_bp.py` |
| Web 管理后台路由 | `service/deskbot-server/src/deskbot_server/web/blueprints/app_bp.py` |
| 2C 页面/API 路由 | `service/deskbot-server/src/deskbot_server/web/blueprints/app2c_bp.py` |
| Web 调试路由 | `service/deskbot-server/src/deskbot_server/web/blueprints/debug_bp.py` |
| Web 到 deskbot 代理 | `service/deskbot-server/src/deskbot_server/web/blueprints/proxy_bp.py` |
| deskbot WS 路由分发 | `service/deskbot-server/src/deskbot_server/ws/router.py` |
| deskbot HTTP API | `service/deskbot-server/src/deskbot_server/ws/http_api.py` |
| `/asr_chat` 协议 | `service/deskbot-server/src/deskbot_server/ws/asr_chat.py` |
| `/device_pipeline` 协议 | `service/deskbot-server/src/deskbot_server/ws/device_pipeline.py` |
| `/camera_view` 协议 | `service/deskbot-server/src/deskbot_server/ws/camera.py` |
| TTS 侧车入口 | `service/paddlespeech-server/src/paddlespeech_server/main.py` |
| 音素 TTS 路由 | `service/paddlespeech-server/src/paddlespeech_server/ws_phoneme.py` |
