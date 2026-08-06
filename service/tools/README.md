# 本地调试工具

在 `.venv` 激活后运行。

- **模拟设备主链路**（与固件一致）：URL 带 `device_id` 与 **`pin_code`**（4 位，如 `1234`），**不要**用 `api_key`。
- **调试订阅 / HTTP**：才使用 `data/.free_api_key` 中的 Key。

```bash
source .venv/bin/activate

# 推送 wav 测 /asr_chat 全链路（设备侧鉴权）
python tools/test_client.py \
  --ws-url "ws://127.0.0.1:9000/asr_chat?device_id=deskbot_dev&pin_code=1234" \
  --input-wav demo_16k_mono.wav

# 本机麦克风
python tools/live_mic_client.py \
  --ws-url "ws://127.0.0.1:9000/asr_chat?device_id=deskbot_dev&pin_code=1234"

# 推图片测 camera_frame
python tools/camera_test_client.py \
  --ws-url "ws://127.0.0.1:9000/asr_chat?device_id=deskbot_dev&pin_code=1234" \
  --image-dir ./samples

# 设备-服务端网络连通性 / 并发 / PB 延迟（须真实设备在线）
python tools/network_connectivity_test.py \
  --device-id deskbot_e8f60a8cf9b0 \
  --base-url http://127.0.0.1:9000 \
  --concurrent-sec 20
```

WAV 须 **16 kHz / mono / s16le**。
