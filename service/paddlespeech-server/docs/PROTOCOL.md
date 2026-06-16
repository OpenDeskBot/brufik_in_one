# streaming_phoneme WebSocket 协议

TTS 侧车由仓库根目录 `./start.sh` 与 deskbot-server 一并启动。部署说明见 [README.md](../../README.md)。

路径：`/paddlespeech/tts/streaming_phoneme`

传输：WebSocket JSON 文本帧（与官方 PaddleSpeech streaming TTS 握手风格一致）。

**引擎限制**：仅支持 `engine_type=online-onnx`（见 `conf/tts_online_application.yaml` 中 `tts_online-onnx`）。

## 对齐语义

1. 服务端对输入文本做 frontend 音素序列（`phone_ids`）。
2. 调用与官方 `/paddlespeech/tts/streaming` 相同的 ONNX 流式合成，收集整段 int16 PCM。
3. 将 PCM **按音素个数均分**为 N 段，每段附带 `phoneme_id`、`phoneme`（符号）、`ms`（段时长毫秒）、`audio`（该段 PCM 的 base64）。

流式 voc 不提供逐音素真实边界，因此 `ms` 为均分启发值，仅供口型 / 动画时间轴近似对齐。

## 会话流程

```
Client                          Server
  |-- {"signal":"start"} ------->|
  |<-- status=0, session --------|
  |-- {"text":"...", "spk_id":0}->|
  |<-- status=1, segments[] -----|  合成结果（音素分片）
  |<-- status=2, segments=[] -----|  结束标记
  |-- {"signal":"end"} --------->|
  |<-- status=0, 关闭提示 -------|
```

客户端也可附带 `task` 字段（如 `{"task":"tts","signal":"start"}`），服务端**只读取** `signal` / `text` / `spk_id` / `session`，额外字段被忽略。

## 请求 JSON

### 握手

```json
{"signal": "start"}
```

### 合成

```json
{"text": "你好世界", "spk_id": 0}
```

- `text`：待合成文本（必填）
- `spk_id`：说话人 id，默认 `0`

### 结束

```json
{"signal": "end"}
```

可选携带 `session`（与握手返回一致），便于客户端日志关联。

## 响应 JSON

通用字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | int | `0` 控制面成功；`1` 合成数据；`2` 合成结束；`-1` 错误 |
| `message` | str | 错误描述（`status=-1`） |
| `segments` | array | 音素分片列表 |
| `signal` | str | 控制面提示（握手 / 关闭） |
| `session` | str | 会话 id |

### `status=0` 握手成功

```json
{
  "status": 0,
  "signal": "server ready",
  "session": "a1b2c3..."
}
```

### `status=1` 合成结果

```json
{
  "status": 1,
  "segments": [
    {
      "phoneme_id": 42,
      "phoneme": "n",
      "ms": 120,
      "audio": "<base64 int16 PCM chunk>"
    }
  ]
}
```

- `audio`：小端 int16 PCM 单声道片段，与官方 streaming 帧编码一致
- `ms`：该段时长（毫秒），至少为 1（非空段）

### `status=2` 合成结束

```json
{"status": 2, "segments": []}
```

### `status=-1` 错误

```json
{
  "status": -1,
  "message": "send signal start first",
  "segments": []
}
```

常见错误：

| message | 原因 |
|---------|------|
| `tts engine not initialized` | 引擎池未就绪 |
| `streaming_phoneme only supports engine_type=online-onnx` | yaml 引擎类型不匹配 |
| `send signal start first` | 未握手即发送 `text` |
| `empty phone_ids for text` | frontend 未解析出音素 |
| `invalid request json` | 缺少 `signal` 与 `text` |

官方 `/paddlespeech/tts/streaming` 协议未改，见 PaddleSpeech 文档。联调：`tools/test_phoneme_client.py`。
