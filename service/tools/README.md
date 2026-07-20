# 本地调试工具

在 `.venv` 激活后运行。WebSocket URL **须带 `device_id` 与 `api_key`**（可用 `data/.free_api_key` 中的免费 Key）。

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

# 设备-服务端网络连通性 / 并发 / PB 延迟（须真实设备在线）
python tools/network_connectivity_test.py \
  --device-id deskbot_e8f60a8cf9b0 \
  --base-url http://127.0.0.1:9000 \
  --concurrent-sec 20
```

WAV 须 **16 kHz / mono / s16le**。
