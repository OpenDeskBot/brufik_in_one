# Brufik 一体化仓库

本仓库合并了 **Brufik 硬件/固件** 与 **opendesk-service 语音后台**。

| 目录 | 说明 |
|------|------|
| [`hardware/`](hardware/) | ESP32S3 固件、机械结构、PCB 重建文件、烧录脚本 |
| [`service/`](service/) | 语音后台：VAD → ASR → LLM → TTS，WebSocket `/asr_chat`，Web 控制台 |

## 快速开始

### 1. 启动后台（service）

```bash
cd service
cp deskbot-server/.env.example deskbot-server/.env
# 编辑 deskbot-server/.env，填写 LLM_API_KEY
chmod +x start.sh
./start.sh
```

控制台：`http://<本机IP>:5050/` · 设备 WebSocket：`ws://<本机IP>:9000/asr_chat`

详见 [`service/README.md`](service/README.md)。

### 2. 烧录固件（hardware）

```bash
cd hardware
# 编辑 firmware/deskbot_config.h — WiFi、WS 地址、API Key
./flash_rom.sh all
```

详见 [`hardware/README.md`](hardware/README.md) 与 [`hardware/README_zh.md`](hardware/README_zh.md)。

## 许可证

- **硬件设计**（[`hardware/mechanical/`](hardware/mechanical/)）：CERN-OHL-S-2.0
- **固件**（[`hardware/firmware/`](hardware/firmware/)）：GPL-3.0
- **后台**（[`service/`](service/)）：GPL-3.0

各子目录内有对应 `LICENSE` 文件。
