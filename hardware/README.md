# Brufik

[中文](README_zh.md) | English

**Hardware in this repository is under [CERN-OHL-S-2.0](mechanical/LICENSE); software is under [GPL-3.0](firmware/LICENSE).**

**Brufik** is an open-source deskbot on **Seeed XIAO ESP32S3 Sense**. Backend: [open-deskbot-service](https://github.com/OpenDeskBot/open-deskbot-service).

![Brufik deskbot — assembled unit](mechanical/poster.jpg)

---

## 1. Out of the box

1. Set your router or phone hotspot to **`deskbot_wifi` / `hello2026`** (defaults in [`firmware/deskbot_config.h`](firmware/deskbot_config.h)).
2. Power on — the device joins that WiFi and talks to your backend.
3. To use another SSID: power on the deskbot, join AP **`Deskbot_Rom`**, then open the address shown on the screen, usually **`http://192.168.4.1/`**. The onboarding page lets you pick or type your home WiFi and save it. You can still edit `WIFI_DEFAULT_*` and `DESKBOT_WS_*` before flashing.

---

## 2. Developers

Edit [`firmware/deskbot_config.h`](firmware/deskbot_config.h), then:

```bash
./flash_rom.sh all
```

Deploy [open-deskbot-service](https://github.com/OpenDeskBot/open-deskbot-service). Firmware WebSocket: **`/asr_chat`**.

Optional debug: `http://<device-ip>/` for a local camera page.

---

## 3. DIY assembly

| Part | Notes | Search terms |
|------|-------|----------------|
| MCU | Camera module + **onboard mic** on the Sense board (**no extra purchase**) | Seeed XIAO ESP32S3 **Sense** |
| Lens | For **OV2640**; **120°** wide angle; **same-plane** (not off-axis); **25 mm** length | `OV2640 lens 120° same plane 25mm` |
| LCD | 1.83" SPI ST7789 240×284 | Waveshare 1.83 LCD Rev2 |
| Servos | **Large + small**; pan = **2g micro servo**, tilt = larger servo | `2g servo`, `SG90` / `9g servo` |
| Amp | I2S | MAX98357A |
| Speaker | **2011** type | `2011 speaker`, `8Ω 2011` |
| Power | 5V ≥1A for servos | — |
| PCB | This repo includes an extension **PCB** for easier wiring; **hand-wiring without the PCB also works** | — |

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
| Servo X (pan) | PWM | **D7** (2g) |
| Servo Y (tilt) | PWM | **D6** (large) |
| MAX98357 | DIN/BCLK/LRC | D0 / D5 / D4 → 2011 speaker |
| Mic | PDM | **Onboard** (ESP32S3 Sense) |

Details: [`firmware/deskbot_config.h`](firmware/deskbot_config.h).

---

## License

| Scope | License | File |
|-------|---------|------|
| Hardware ([`mechanical/`](mechanical/)) | CERN-OHL-S-2.0 | [`mechanical/LICENSE`](mechanical/LICENSE) |
| Software ([`firmware/`](firmware/) etc.) | GNU GPL v3.0 | [`firmware/LICENSE`](firmware/LICENSE) |
