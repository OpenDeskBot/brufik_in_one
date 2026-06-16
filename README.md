# Brufik in One

Monorepo combining **Brufik hardware/firmware** and **opendesk-service backend**.

| Directory | Description |
|-----------|-------------|
| [`hardware/`](hardware/) | ESP32S3 firmware, mechanical assets, PCB rebuild files, flash scripts |
| [`service/`](service/) | Voice backend: VAD → ASR → LLM → TTS, WebSocket `/asr_chat`, web console |

## Quick start

### 1. Backend (service)

```bash
cd service
cp deskbot-server/.env.example deskbot-server/.env
# Edit deskbot-server/.env — set LLM_API_KEY
chmod +x start.sh
./start.sh
```

Web console: `http://<host>:5050/` · Device WS: `ws://<host>:9000/asr_chat`

See [`service/README.md`](service/README.md).

### 2. Firmware (hardware)

```bash
cd hardware
# Edit firmware/deskbot_config.h — WiFi + WS host + API key
./flash_rom.sh all
```

See [`hardware/README.md`](hardware/README.md) and [`hardware/README_zh.md`](hardware/README_zh.md).

## License

- **Hardware** ([`hardware/mechanical/`](hardware/mechanical/)): CERN-OHL-S-2.0
- **Firmware** ([`hardware/firmware/`](hardware/firmware/)): GPL-3.0
- **Service** ([`service/`](service/)): GPL-3.0

See respective `LICENSE` files in each subtree.
