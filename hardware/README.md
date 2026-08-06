# Brufik

[中文](README_zh.md) | English

**Hardware in this repository is under [CERN-OHL-S-2.0](mechanical/LICENSE); software is under [GPL-3.0](firmware/LICENSE).**

**Brufik** is an open-source deskbot on **Seeed XIAO ESP32S3 Sense**. Backend lives in this monorepo: [`../service/`](../service/).

![Brufik deskbot — assembled unit](mechanical/poster.jpg)

---

## Toolchain

| Item | Value |
|------|--------|
| Board | Seeed XIAO ESP32S3 Sense |
| Platform | [pioarduino](https://github.com/pioarduino/platform-espressif32) **55.03.39** (do **not** use stock PlatformIO `espressif32` Arduino 2.0.17 / IDF 4.4.7) |
| Framework | Arduino-ESP32 **3.3.9** + ESP-IDF **5.5.4** |
| Host Python | **≥3.10** (recommend `python3.11 -m venv hardware/.venv` then `pip install platformio`) |
| Flash | [`./flash_rom.sh`](flash_rom.sh) (prefers `.venv/bin/pio`; ports include `/dev/tty.usbmodem*`) |

Audio uses Arduino **`ESP_I2S`** (PDM RX mic + STD TX amp). Camera uplink prefers hardware JPEG, else RGB565 + `frame2jpg` (AWB warmup frames after init). `device_id` comes from `esp_read_mac` (`deskbot_<mac>`).

---

## 1. Out of the box

1. Edit [`firmware/deskbot_config.h`](firmware/deskbot_config.h): set `WIFI_DEFAULT_*` to your AP (or set the AP to match those macros), and set `DESKBOT_WS_HOST` / `DESKBOT_WS_PORT` to your backend.
2. Power on — the device joins WiFi and opens `ws://…/asr_chat?device_id=…&pin_code=…` (PIN is shown on the boot screen; **no API key** on the device link).
3. To change WiFi later: power on, join the open soft-AP whose SSID is the **`device_id`** (e.g. `deskbot_e83dc1faea30`), then open the URL on the screen (usually **`http://192.168.4.1/`**).

---

## 2. Developers

```bash
# from hardware/
./flash_rom.sh all          # build + upload + monitor
./flash_rom.sh build
./flash_rom.sh upload [port]
./flash_rom.sh log [port]
```

Backend: [`../service/`](../service/). Firmware WebSocket: **`/asr_chat`**. Camera frames go over the same WS (`camera_frame`); there is **no** always-on local camera HTTP page after STA is up (provisioning portal only during AP mode). Debug JPEG preview is on the server: `/camera_view`.

WiFi write-timeout patches (Arduino 3.x) target `NetworkClient.cpp` (fallback `WiFiClient.cpp`); see `scripts/`.

---

## 3. DIY assembly

| Part | Notes | Search terms |
|------|-------|----------------|
| MCU | Camera module + **onboard mic** on the Sense board (**no extra purchase**) | Seeed XIAO ESP32S3 **Sense** |
| Lens | For **OV2640**; **120°** wide angle; **same-plane** (not off-axis); **25 mm** length | `OV2640 lens 120° same plane 25mm` |
| LCD | 1.83" SPI ST7789 240×284 | Waveshare 1.83 LCD Rev2 |
| Servos | **Large + small**; pan = **2g micro servo**, tilt = larger servo | `2g servo`, `SG90` / `9g servo` |
| Amp | I2S (MAX98357A) | MAX98357A |
| Speaker | **2011** type | `2011 speaker`, `8Ω 2011` |
| Power | 5V ≥1A for servos | — |
| PCB | Extension **PCB** optional; **hand-wiring also works** | — |

### Assembly guide & reference photos

- **Step-by-step manual:** [`mechanical/说明书1.02PDF.pdf`](mechanical/说明书1.02PDF.pdf)
- **All parts laid out:** [`mechanical/parts-overview.png`](mechanical/parts-overview.png)
- **Core assembly done (shell not installed):** [`mechanical/assembly-without-shell.png`](mechanical/assembly-without-shell.png)
- **Side view without shell:** [`mechanical/assembly-side-no-shell.png`](mechanical/assembly-side-no-shell.png)

### Wiring (XIAO pad → device)

> Schematic **IO8 / IO3** = **GPIO numbers**, not silkscreen D8/D3.

| Device | Signals | XIAO pads |
|--------|---------|-----------|
| LCD SPI | MOSI/CLK/CS/DC | D10 / D8 / D1 / D2 |
| Servo X (pan) | PWM | **D9** (GPIO8, 2g) — avoids UART0 on D6/D7 |
| Servo Y (tilt) | PWM | **D3** (GPIO4, large) |
| MAX98357 | DIN/BCLK/LRC | D0 / D5 / D4 → 2011 speaker |
| Mic | PDM | **Onboard** (ESP32S3 Sense) |

Details: [`firmware/deskbot_config.h`](firmware/deskbot_config.h). Older Board1 PCB silk may route servos to D6/D7; current firmware uses **D9/D3** — see [`mechanical/pcb/Board1/README_zh.md`](mechanical/pcb/Board1/README_zh.md).

---

## License

| Scope | License | File |
|-------|---------|------|
| Hardware ([`mechanical/`](mechanical/)) | CERN-OHL-S-2.0 | [`mechanical/LICENSE`](mechanical/LICENSE) |
| Software ([`firmware/`](firmware/) etc.) | GNU GPL v3.0 | [`firmware/LICENSE`](firmware/LICENSE) |
