# ESP32 与 BotServer 通信协议（pb v2.1）

单条 WebSocket：`ws://<host>:9000/asr_chat?device_id=<id>&api_key=<key>`。文本 JSON 与 binary 交替，**不用 base64**。长度由 **`next_bin_len`** 声明（上行在 JSON 根；下行在 `audio.next_bin_len`）。旧版 `audio.next_bin: 1` 已废弃。

> **相关文档：** 部署与快速使用见 [README.md](../README.md)；主服务配置见 [SERVER.md](./SERVER.md)。定时任务、长期记忆、控制台管理等为**服务端能力**，不扩展本设备协议字段。

默认 **`asr_chat_device_pb_only: true`**：设备下行只处理 **`pb_*` + PCM**。服务端不再下发 `face_info`、`asr_*`、`llm_text`、TTS/错误类 JSON（如 `playback_done`、`tts_error`、`error`、`asr_rejected` 等）；轮次结果与耗时见 `/device_pipeline` 的 `pipeline_event` 或 Web 调试台。

---

## 0. 鉴权与配额（设备端必读）

### 0.1 适用范围

| 通道 | 路径 | 是否需要 API Key |
|------|------|------------------|
| 生产主链路 | `/asr_chat` | **必须** |
| 摄像头预览 | `/camera_view` | **必须** |
| 流水线订阅 | `/device_pipeline` | **必须** |
| HTTP API | `/api/*`（非本机） | **必须** |
| 健康检查 | `/health` | 否 |

本机调试代理（Flask `:5050` → deskbot）从 `127.0.0.1` 转发时免 Key；**ESP32 直连 `:9000` 必须携带 Key**。

### 0.2 传递方式（二选一）

**方式 A — URL 查询参数（推荐固件实现）**

```
ws://<host>:9000/asr_chat?device_id=deskbot_abc123&api_key=odk_xxxxxxxx
```

别名也支持：`apikey`、`key`。

**方式 B — WebSocket 握手 Header**

```
X-API-Key: odk_xxxxxxxx
```

或 `Authorization: Bearer odk_xxxxxxxx`。

### 0.3 Key 类型

| 类型 | 前缀 | 说明 |
|------|------|------|
| 用户 Key | `odk_` | 控制台「账号设置」生成，配额不限（默认） |
| 免费 Key | `odk_free_` | 服务器 `data/.free_api_key`，**每日 1GB 总字节** |

### 0.4 失败行为

| 情况 | WebSocket | HTTP |
|------|-----------|------|
| 缺少/无效 Key | 关闭码 **1008**，reason `api_key_required` | `401` + `api_key_required` |
| 免费 Key 超额 | 关闭码 **1008**，reason `quota_exhausted` | `429` + `quota_exhausted` |

连接前服务端会预检配额；轮次中（ASR/人脸/LLM/TTS）超额后当轮记日志，后续请求继续受配额约束。

### 0.5 用量计量（按 Key + device_id）

每轮对话按字节累计四类消耗，并同时记入 **API Key** 与 **device_id**（控制台「用量看板」可查看）：

| 类别 | 计量来源 |
|------|----------|
| ASR | 上行 PCM 段字节数 |
| 人脸 | `camera_frame` JPEG 字节数 |
| LLM | 识别文本 + 模型回复 UTF-8 字节 |
| TTS | 回复文本 UTF-8 字节 × 48（估算） |

**固件务必在 URL 中带稳定 `device_id`**，否则不会出现在设备列表，也无法按设备统计用量。

### 0.6 固件最小接入示例

```c
// 伪代码
const char *url =
  "ws://192.168.1.10:9000/asr_chat"
  "?device_id=deskbot_1cdbd476ab5c"
  "&api_key=odk_free_xxxxxxxx";
websocket_connect(url);
```

---

## 1. 传输规则

| 规则 | 说明 |
|------|------|
| 成对顺序 | 若 JSON 声明 `next_bin_len > 0`，下一条 WS **必须**为等长 binary，再收下一条 JSON |
| 错位 | 预期 binary 却收到 JSON，或长度不符 → 丢弃该帧/序列，勿把 JPEG 送进 ASR |
| 独立 `/camera` | 已移除（连接返回 1008） |
| 兼容 | 裸 binary 无 JSON 仍当旧版音频（新固件勿用） |

**上下行对照**

| 方向 | 声明 | binary 内容 |
|------|------|-------------|
| 上行 | 根级 `"next_bin_len": N` | Opus / PCM16 / JPEG |
| 下行 pb | `"audio": {"next_bin_len": N}` | s16le PCM |

---

## 2. 上行（设备 → 服务端）

### 2.1 音频 `audio`

`codec` 与 `config.yaml` → `audio.input_codec` 一致（常见 **opus** @ 16 kHz）。

```json
{ "type": "audio", "codec": "opus", "next_bin_len": 80 }
```

下一条 80 字节 Opus。PCM 示例：`codec":"pcm16"`, `sr`:16000, `ch`:1。

**段结束**（无 binary）：

```json
{ "type": "flush" }
```

触发 ASR → LLM → TTS → pb 下行。

### 2.2 画面 `camera_frame`

```json
{ "type": "camera_frame", "codec": "jpeg", "next_bin_len": 12345, "seq": 42 }
```

下一条为 JPEG。下行 pb 可用 **`cam_fps`**（如 `3` = 每秒 3 帧）调节上行帧率；发送侧建议丢旧保新。

### 2.3 其它

| `type` | 说明 |
|--------|------|
| `ping` | 服务端回 `pong` |
| `pb_ack` | 播放回压，见 §8 |
| `user_text` | 调试：跳过 ASR |

**示例顺序**

```
JSON audio + next_bin_len → BIN opus
JSON camera_frame → BIN jpeg
JSON flush
```

---

## 3. 下行 pb 概述

| 项目 | 约定 |
|------|------|
| 版本 | `pb_ver: 2`（wire v2.1） |
| 音频 | mono **s16le**，**sr = 24000**（首包声明） |
| 画布 | **284 × 240**，原点左上 |
| 单包 | `chunk_ms ≤ 10000` |
| 口播默认 | `level = 1`，`action = "replace"` |

一条 pb JSON 可含 **`anim[]`**（表情）、**`servo[]`**（舵机）、**`audio`**（PCM 长度）。口播由服务端按音素组帧后合并为多片 `pb_start` → `pb_chunk*` → `pb_end`，或单片 **`pb_single`**。

**全局规则（固件必实现）**

| 编号 | 规则 |
|------|------|
| R0 | `pb_start`/`pb_chunk`/`pb_end`/`pb_single` 中 `audio`/`servo`/`anim` 至少一项 |
| R1 | 队列决策仅 **`pb_start`**、**`pb_single`**；同 `req` 的 `pb_chunk`/`pb_end` 只续传 |
| R2 | `audio.next_bin_len > 0` → 下一条为等长 PCM binary |
| R3 | 协议错位 → 丢弃该 `req` 剩余片，清队列 |
| R4 | 同 `req` 内 `idx` 从 0 严格递增 |
| R5 | 有 `anim[]` 时 `sum(anim[i].ms) == chunk_ms` |
| R6 | PCM 字节数 `== (chunk_ms * sr // 1000) * 2` |

24 kHz：`chunk_ms=113` → 5424 字节；`chunk_ms=1921` → 92112 字节。固件 WS RX 缓冲须能容纳单帧 binary（默认可至约 480000 字节 / 10s）。

---

## 4. 下行消息类型

| `type` | 用途 |
|--------|------|
| `pb_start` | 链首包（`idx=0`），触发队列；含音频时带 `sr`/`fmt`/`ch` |
| `pb_chunk` | 链中间包 |
| `pb_end` | 链末包 |
| `pb_single` | 整轮仅一条（idle、单段舵机、或口播仅一包） |
| `pb_cancel` | 中止 `req` |

多片：`pb_start`(0) → `pb_chunk`(1…N-2) → `pb_end`(N-1)。单片只发 **`pb_single`**，禁止无 `pb_start` 单发 `pb_end`。

### 4.1 公共字段

| 字段 | 说明 |
|------|------|
| `req` | 序列 ID（16 位 hex 常见） |
| `idx` | 分片序号，从 0 递增 |
| `chunk_ms` | 本片时长（ms） |
| `level` | 优先级 0–3：0 idle，1 口播，2 紧急，3 调试 |
| `action` | `replace`（默认）\| `append` \| `default` |
| `volume` | 0–100，可选；同 `req` 后续 PCM 按此音量 |
| `cam_fps` | >0 时设置 JPEG 上行目标帧率 |

忽略以 `_` 开头的键。

### 4.2 时序示例

```
→ JSON pb_start  audio.next_bin_len=N  chunk_ms=T  anim[…]
→ BINARY N 字节 PCM
→ JSON pb_chunk …
→ BINARY …
→ JSON pb_end …
→ BINARY …
```

---

## 5. 动画 `anim[]`

`anim` **必须是数组**。每项：

| 字段 | 说明 |
|------|------|
| `elements` | 图层容器（见下表） |
| `ms` | 该段子动画时长（≥1） |
| `phoneme` | 可选，音素符号（调试） |

### 5.1 `elements` 图层

| 键 | 说明 |
|----|------|
| `bg` | 背景（最先绘制） |
| `nose` | 鼻 |
| `mouth` | 口型 |
| `eye_l` / `eye_r` | 左/右眼 |
| `extra` | 装饰（腮红、文字等） |

### 5.2 片内时间轴

```
t = 0
for k in 0 .. anim.length-1:
  在 [t, t + anim[k].ms) 绘制 anim[k].elements
  t += anim[k].ms
```

有 PCM 时子动画切换与采样边界对齐。

### 5.3 绘制顺序

**`bg` → `nose` → `mouth` → `eye_l` → `eye_r` → `extra`**

### 5.4 图元与颜色

每个图元须有 **`shape`**（比较前转小写、做别名归一化）。wire 上颜色为 **`c`（RGB565 整数）**；配置 JSON 可写 `#RGB` / 命名色，服务端转换；缺省 **65535（白）**。

```
R5 = (R8 >> 3) & 0x1F;  G6 = (G8 >> 2) & 0x3F;  B5 = (B8 >> 3) & 0x1F
c  = (R5 << 11) | (G6 << 5) | B5
```

未知 `shape`：**跳过**该图元，不判整包失败。

### 5.5 `shape` 对照表

| 主 `shape` | 别名（等价） | 必填字段 |
|------------|--------------|----------|
| `rect` | `fill_rect`, `fillRect` | `x`,`y`,`w`,`h` |
| `rect_outline` | `draw_rect`, `drawRect` | 同上 |
| `circle` | `fill_circle`, `fillCircle` | `x`,`y`,`r` |
| `circle_outline` | `draw_circle`, `drawCircle` | 同上 |
| `line` | `drawLine` | `x1`,`y1`,`x2`,`y2` |
| `pixel` | `point`, `drawPixel` | `x`,`y` |
| `hline` | `h_line`, `drawFastHLine` | `x`,`y`,`w` |
| `vline` | `v_line`, `drawFastVLine` | `x`,`y`,`h` |
| `ellipse` / `ellipse_fill` | `drawEllipse` / `fillEllipse` | `x`,`y` + (`rw`,`rh` 或 `w`,`h` 作半轴) |
| `triangle` / `triangle_fill` | `drawTriangle` / `fillTriangle` | `x0,y0,x1,y1,x2,y2` 或第一点 `x,y` |
| `round_rect` / `round_rect_outline` | `fillRoundRect` / `drawRoundRect` | `x`,`y`,`w`,`h`, `radius` 或 `r` |
| `rotated_rect_outline` / `rotated_rect_fill` | — | `x`,`y`,`w`,`h`,`angle`（**中心**坐标） |
| `text` | — | `x`,`y`,`text`,`size`,`c` |
| `image` | — | `asset`（0-based 下标）+ 见 §6.2 |

三角形勿与 `rect` 的 `w`,`h` 混淆。

### 5.6 可选 `assets[]`（JPEG 等）

若存在 `assets[]`，读完 PCM 后按 `assets[i].next_bin_len` 依次读 binary；`shape: image` 的 `asset` 指向下标。

---

## 6. 音频与舵机

### 6.1 PCM

```json
"audio": { "next_bin_len": 92112 }
```

`sr`/`fmt`/`ch` 以本 `req` **首条含音频**的包为准（当前 24000 / s16le / 1）。

### 6.2 舵机 `servo[]`

```json
"servo": [
  { "xm": 1, "ym": 1, "x": 0, "y": 30, "ms": 380 }
]
```

| `xm`/`ym` | 0 绝对；1 相对增量；2 本轴保持 |
|-----------|------------------------------|

与 `anim[]` **并行**调度；无舵机时省略 `servo` 键。

---

## 7. 优先级队列

收到 **`pb_start` / `pb_single`** 时按 `level`（0–3）与 `action` 决策：

| 条件 | 行为 |
|------|------|
| `level` > `queue_level` | 清空队列，立即执行 |
| `level` == `queue_level` 且 `replace` | 清空后执行 |
| `append` | 追加队尾 |
| `default` | 队列中更高优先级序列 >1 条则丢弃，否则同 append |

---

## 8. 回压与取消

**上行 `pb_ack`**

```json
{
  "type": "pb_ack",
  "req": "a1b2c3d4e5f67890",
  "idx": 2,
  "audio_buf_ms": 360
}
```

可选上行 **`servo` object**（单对象，非数组）反馈当前位置与软限位。

**下行 `pb_cancel`**

```json
{ "type": "pb_cancel", "req": "a1b2c3d4e5f67890" }
```

---

## 9. 表情配置（服务端生成 `anim[]`）

口播时服务端从 **`face_bundle`** JSON（`tts.pb_face_bundle_json` 或 `DESKBOT_PB_FACE_BUNDLE_JSON`）按音素查表组帧；保存文件后 **mtime 热重载**。示例：`data/global/deskbot-face.json`（`phonemes` + `emotions`）。

| 顶层键 | 说明 |
|--------|------|
| `mouth_by_phoneme` | 音素 → 口型 `{ elements[], offset? }` |
| `mouth_by_phoneme_groups` | 共享条：`states[]` + `elements` + `offset` |
| `eye_l` / `eye_r` | `default` / `open` / `close` 图元数组 |
| `nose` | `default` 图元数组 |
| `extra` | 任意态名 → 图元数组；`metadata.extra_state` 选态 |
| `metadata.blink` | `open_ms` / `close_ms` 控制眨眼相位 |

**offset**：口型 `offset (dx,dy)` 仅平移 **鼻、眼、extra** 坐标；**嘴不动**。未知音素用 `"_"` 或内置默认。

固件只需解析 wire 上的 `anim[].elements`；编辑表情见仓库 `data/`。

---

## 10. 与旧版差异

| 项目 | 旧版 | 当前 |
|------|------|------|
| `anim` | 单对象 `{elements}` | **数组** `[{elements, ms, phoneme?}, …]` |
| `servo` | 单对象 | **数组** |
| 根级 `phoneme` | 有 | **无**（在 `anim[i].phoneme`） |
| 音频长度 | `audio.next_bin: 1` | **`audio.next_bin_len`** 字节数 |
| 颜色 | `color` 字符串 | wire **`c` RGB565** |

---

## 11. 固件实现清单

1. 仅连 `/asr_chat?device_id=`；JSON/binary 状态机。
2. 上行成对发送；下行处理 `pb_*`，按 `audio.next_bin_len` 收 PCM。
3. `anim[]` 按 `ms` 切换；绘制顺序 §5.3；R6 校验 PCM 长度。
4. `pb_start`/`pb_single` 入队（§7）；周期性 `pb_ack`。
5. 未知键、`_` 前缀键忽略；未知 `shape` 跳过。
