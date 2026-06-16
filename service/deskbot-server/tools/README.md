# 本地调试工具

在 `deskbot-server/.venv` 激活后运行。WebSocket URL **须带 `device_id` 与 `api_key`**（可用 `data/.free_api_key` 中的免费 Key）。

```bash
source .venv/bin/activate
KEY="odk_free_xxxx"   # 替换为实际 Key

# 推送 wav 测 /asr_chat 全链路
python tools/test_client.py \
  --ws-url "ws://127.0.0.1:9000/asr_chat?device_id=deskbot_dev&api_key=${KEY}" \
  --input-wav demo_16k_mono.wav

# 本机麦克风
python tools/live_mic_client.py \
  --ws-url "ws://127.0.0.1:9000/asr_chat?device_id=deskbot_dev&api_key=${KEY}"

# 推图片测 camera_frame
python tools/camera_test_client.py \
  --ws-url "ws://127.0.0.1:9000/asr_chat?device_id=deskbot_dev&api_key=${KEY}" \
  --image-dir ./samples
```

WAV 须 **16 kHz / mono / s16le**。
