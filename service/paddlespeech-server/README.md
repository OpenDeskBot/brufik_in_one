# paddlespeech-server

TTS 侧车（默认 **8092**），供 deskbot-server 流式合成与音素口型对齐。通常由仓库根目录 `./start.sh` 自动启动，无需单独操作。

| 端点 | 说明 |
|------|------|
| `/paddlespeech/tts/streaming` | PaddleSpeech 官方流式 TTS |
| `/paddlespeech/tts/streaming_phoneme` | 音素分片 PCM（口型对齐） |

## 启动

```bash
# 推荐：与主服务、Web 控制台一起
cd .. && ./start.sh

# 仅 TTS（开发调试）
./start.sh
SKIP_SETUP=1 ./start.sh
```

要求 Python **3.11**。deskbot-server 通过 `config.yaml` → `tts.ws_url` 连接（默认 `ws://127.0.0.1:8092/paddlespeech/tts/streaming`）。

协议：[docs/PROTOCOL.md](docs/PROTOCOL.md)。部署说明：[../README.md](../README.md)。

```bash
source .venv/bin/activate
python tools/test_phoneme_client.py --text "你好"
```
