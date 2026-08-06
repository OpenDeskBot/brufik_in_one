# Brufik

[English](README.md)

**本仓库中的硬件设计部分遵循 [CERN-OHL-S-2.0](mechanical/LICENSE)，软件部分遵循 [GPL-3.0](firmware/LICENSE)。**

**Brufik** 是一款开源桌面机器人：Seeed XIAO ESP32S3 Sense + 屏 + 舵机 + 麦 + 喇叭。语音与画面走本 monorepo 后台 [`../service/`](../service/)。

![Brufik 桌面机器人 — 组装完成实物](mechanical/poster.jpg)

---

## 工具链

| 项 | 说明 |
|------|------|
| 板型 | Seeed XIAO ESP32S3 Sense |
| Platform | [pioarduino](https://github.com/pioarduino/platform-espressif32) **55.03.39**（**不要**用官方 PlatformIO `espressif32` 的 Arduino 2.0.17 / IDF 4.4.7） |
| 框架 | Arduino-ESP32 **3.3.9** + ESP-IDF **5.5.4** |
| 宿主机 Python | **≥3.10**（推荐 `cd hardware && python3.11 -m venv .venv && .venv/bin/pip install platformio`） |
| 烧录 | [`./flash_rom.sh`](flash_rom.sh)（优先 `.venv/bin/pio`；串口含 `/dev/tty.usbmodem*`） |

音频走 Arduino **`ESP_I2S`**（PDM 收音 + STD 功放）。相机优先硬件 JPEG，否则 RGB565 + `frame2jpg`（init 后丢弃若干帧做 AWB）。`device_id` 由 `esp_read_mac` 生成（`deskbot_<mac>`）。

---

## 一、开箱即用

1. 编辑 [`firmware/deskbot_config.h`](firmware/deskbot_config.h)：把 `WIFI_DEFAULT_*` 改成你家路由，或把热点改成与宏一致；并设置 `DESKBOT_WS_HOST` / `DESKBOT_WS_PORT`。
2. 上电后连 WiFi，并连接 `ws://…/asr_chat?device_id=…&pin_code=…`（开机屏会显示 PIN；**设备链路不用 API Key**）。
3. 改家里 WiFi：上电后连接开放热点，SSID 为 **`device_id`**（如 `deskbot_e83dc1faea30`），浏览器打开屏幕地址（通常 **`http://192.168.4.1/`**）按 onboarding 保存。

---

## 二、本地开发者

**需要：** USB、PlatformIO（Python ≥3.10）、串口权限（Linux `dialout`）。

```bash
# 在 monorepo 的 hardware/ 目录
./flash_rom.sh all          # 编译 + 烧录 + 监视
./flash_rom.sh build
./flash_rom.sh upload [端口]
./flash_rom.sh log [端口]
```

| 命令 | 说明 |
|------|------|
| `./flash_rom.sh build` | 编译 |
| `./flash_rom.sh upload [端口]` | 烧录 |
| `./flash_rom.sh log [端口]` | 串口监视 |
| `./flash_rom.sh all [端口]` | 烧录 + 监视 |

后台见 [`../service/`](../service/)。固件 WebSocket：**`/asr_chat`**。摄像头 JPEG 经同一 WS 上行（`camera_frame`）；STA 正常运行后**没有**常驻本机摄像头网页（仅配网 AP 门户）。服务端调试预览：`/camera_view`。

Arduino 3.x 的 WiFi write timeout 补丁优先改 `NetworkClient.cpp`（见 `scripts/`）。

---

## 三、自行组装

### 元器件与采购关键词

| 部件 | 说明 | 搜索关键词（淘宝/嘉立创等） |
|------|------|---------------------------|
| 主控 | 带摄像头模组 + **板载麦克风**（Sense 扩展板上，**无需另购**） | `Seeed XIAO ESP32S3 Sense`、`Seeed Studio XIAO ESP32S3 Sense` |
| 镜头 | **OV2640** 用；广角 **120°**；**同面**（勿买异面）；长度 **25 mm** | `OV2640 镜头 120度 同面 25mm` |
| 屏幕 | 1.83 寸 SPI，**ST7789** 240×284 | `微雪 1.83寸 LCD`、`Waveshare 1.83 LCD Rev2`、`ST7789 240x284` |
| 舵机 | **一大一小**：俯仰用大舵机，水平用 **2g 舵机** | `2g 舵机`、`微型舵机 2g`；大舵机可用 `SG90` / `9g 舵机` 等 |
| 功放 | I2S | `MAX98357A`、`MAX98357 模块` |
| 喇叭 | **2011** 腔体喇叭 | `喇叭 2011`、`2011 喇叭`、`8Ω 2011 扬声器` |
| 杜邦线 / 细线 | 信号与电源 | `杜邦线`、`硅胶线 26AWG` |
| 电源 | 5V 给舵机（≥1A），3.3V 给板子/屏 | `5V 1A 电源模块`、`USB 5V` |
| PCB | 本项目提供一块扩展 **PCB**，可简化接线；**不用 PCB 也可自行飞线焊接** | — |

### 组装说明与参考图

- **图文说明书：** [`mechanical/说明书1.02PDF.pdf`](mechanical/说明书1.02PDF.pdf)
- **零件全图：** [`mechanical/parts-overview.png`](mechanical/parts-overview.png)
- **基本组装完成（未装外壳）：** [`mechanical/assembly-without-shell.png`](mechanical/assembly-without-shell.png)
- **未装外壳侧面：** [`mechanical/assembly-side-no-shell.png`](mechanical/assembly-side-no-shell.png)

### 接线（XIAO 丝印 → 外设）

> 微雪图纸上的 **IO8 / IO3** 指 **ESP32 GPIO 号**，不是丝印 **D8 / D3**。

| 外设 | 信号 | 接 XIAO 焊盘 | 备注 |
|------|------|--------------|------|
| **LCD** | MOSI / SCK / CS / DC | **D10 / D8 / D1 / D2** | SPI |
| **舵机 左右 (X)** | PWM | **D9**（GPIO8） | 小 2g；避开 D6/D7（UART0） |
| **舵机 上下 (Y)** | PWM | **D3**（GPIO4） | 大舵机 |
| **MAX98357** | DIN / BCLK / LRC | **D0 / D5 / D4** | I2S → 2011 喇叭 |
| **麦克风** | PDM | **板载**（ESP32S3 Sense 主板上） | 无需外接 INMP441 |
| **舵机电源** | 5V / GND | 独立 5V≥1A | 与逻辑共地 |

引脚宏见 [`firmware/deskbot_config.h`](firmware/deskbot_config.h)。旧版 Board1 转接板网表可能把舵机接到 D6/D7；**当前固件为 D9/D3**，详见 [`mechanical/pcb/Board1/README_zh.md`](mechanical/pcb/Board1/README_zh.md)。

---

## 许可证

| 范围 | 协议 | 文件 |
|------|------|------|
| 硬件设计（[`mechanical/`](mechanical/)） | CERN-OHL-S-2.0 | [`mechanical/LICENSE`](mechanical/LICENSE) |
| 软件（[`firmware/`](firmware/) 等） | GNU GPL v3.0 | [`firmware/LICENSE`](firmware/LICENSE) |
