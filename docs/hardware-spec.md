# Deskbot 硬件规格文档

> 产品名称：Deskbot（内部）/ Brufik（公开仓库）
> 固件版本：0.0.5

---

## 1. 主控平台

| 项目 | 规格 |
|------|------|
| 开发板 | **Seeed XIAO ESP32S3 Sense** |
| MCU | ESP32-S3（Xtensa 双核 LX7） |
| 框架 | Arduino-ESP32 3.3.9 + ESP-IDF 5.5.4 |
| PlatformIO 平台 | pioarduino 55.03.9 |
| PlatformIO 环境名 | `seeed_xiao_esp32s3` |
| Flash 容量 | 8MB |
| PSRAM | 已启用（CONFIG_SPIRAM=y） |
| WiFi | 2.4GHz 802.11 b/g/n（板载） |
| 串口波特率 | 115200（USB CDC） |

### Flash 分区表

| 分区 | 类型 | 偏移 | 大小 |
|------|------|------|------|
| nvs | data/nvs | 0x9000 | 20KB |
| otadata | data/ota | 0xe000 | 8KB |
| app0 | app/factory | 0x10000 | ~7MB |
| ffat | data/fat | 0x700000 | 1MB |

---

## 2. 显示屏

| 项目 | 规格 |
|------|------|
| 型号 | **Waveshare 1.83" LCD Rev2** |
| 驱动 IC | ST7789P（Adafruit_ST7789 驱动） |
| 原始分辨率 | 240 × 284 像素 |
| 显示分辨率 | **284 × 240** 像素（硬件旋转 3，横屏模式） |
| 色深 | RGB565（16-bit） |
| 接口 | SPI Mode 3，80MHz |
| 行偏移 | 36（DESKBOT_DISPLAY_ROW_OFFSET） |
| 列偏移 | 0（DESKBOT_DISPLAY_COL_OFFSET） |
| 亮度 | 最大（寄存器 0x51=255，0x53=0x2C） |
| RST/BL | 硬接 3.3V，不由 MCU 控制 |
| 顶部安全区 | 4 像素 |
| 画布内存 | PSRAM 分配，~133KB |
| 字体 | WenQuanYi 12px 位图字体（GB2312 子集，4400+ 字形） |
| 渲染层 | 6 层矢量动画（bg、nose、mouth、eye_l、eye_r、extra） |

### SPI 引脚分配

| 信号 | GPIO | XIAO Pad |
|------|------|----------|
| MOSI | 9 | D10 |
| SCK | 7 | D8 |
| CS | 2 | D1 |
| DC | 3 | D2 |
| RST | -- | 硬接 3.3V |
| BL | -- | 硬接 3.3V |

---

## 3. 摄像头

| 项目 | 规格 |
|------|------|
| 传感器 | **OV2640** |
| 镜头 | 120° 广角，同平面，25mm 长度 |
| 接口 | 8-bit 并行 + SCCB（I2C）控制 |
| XCLK 频率 | 10MHz（20MHz 会导致 DMA 损坏/绿屏） |
| 帧尺寸 | QVGA 320×240 |
| 像素格式 | 优先硬件 JPEG，回退 YUV422，再回退 RGB565 |
| JPEG 质量 | 18 |
| 最大 JPEG 大小 | 32KB/帧 |
| 帧缓冲 | 2 个缓冲区，位于 PSRAM |
| 抓取模式 | CAMERA_GRAB_WHEN_EMPTY |
| 默认帧率 | 1 FPS（1000ms 间隔，可服务端动态调整） |
| 预热 | 初始化后丢弃 30 帧（AWB/AGC 收敛） |
| 任务栈 | 16KB，优先级 3，绑定 APP_CPU_NUM |

### 摄像头引脚分配

| 信号 | GPIO |
|------|------|
| XCLK | 10 |
| PCLK | 13 |
| VSYNC | 38 |
| HREF | 47 |
| SIOD (SDA) | 40 |
| SIOC (SCL) | 39 |
| Y2 (D0) | 15 |
| Y3 (D1) | 17 |
| Y4 (D2) | 18 |
| Y5 (D3) | 16 |
| Y6 (D4) | 14 |
| Y7 (D5) | 12 |
| Y8 (D6) | 11 |
| Y9 (D7) | 48 |
| PWDN | -1（未使用） |
| RESET | -1（未使用） |

### 摄像头服务端配置

| 参数 | 默认值 | 范围 |
|------|--------|------|
| frame_width | 320 | 160-640 |
| frame_height | 240 | 120-480 |
| horizontal_fov_deg | 120 | 30-170 |
| eye_yaw_range_deg | 50 | 10-90 |
| frontal_angle_threshold_deg | 15 | 1-90 |
| min_face_detection_confidence | 0.15 | - |
| num_faces | 5 | 1-10 |
| face_track_max_dist_px | 90 | 16-240 |
| face_track_max_lost_frames | 18 | 1-60 |
| face_embedding_enabled | true | bool |
| identity_similarity_threshold | 0.40 | 0.25-0.99 |

---

## 4. 舵机

| 项目 | X 轴（水平/左右） | Y 轴（垂直/上下） |
|------|-------------------|-------------------|
| 类型 | 2g 微型舵机 | SG90 / 9g 舵机 |
| GPIO | 8（D9） | 4（D3） |
| 角度范围 | 0° - 180° | 70° - 110°（限位行程） |
| 中心位置 | 90° | 90° |
| 脉冲范围 | 1000μs - 2000μs | 1000μs - 2000μs |
| PWM 频率 | 50Hz（20ms 周期） | 50Hz（20ms 周期） |

- 驱动库：ESP32Servo（基于 MCPWM）
- 运动控制：时间预算线性插值，每 tick 更新
- 命令模式：绝对(0)、相对(1)、保持(2)
- 命令队列深度：50
- 任务栈：8KB，优先级 3，绑定 APP_CPU_NUM

### 舵机服务端配置

| 参数 | 默认值 |
|------|--------|
| xMin | 0 |
| xMax | 180 |
| yMin | 70 |
| yMax | 110 |
| xReverse | 0 |
| yReverse | 0 |
| 视角模式 | viewer（观看者视角） |

---

## 5. 音频

### 扬声器（播放）

| 项目 | 规格 |
|------|------|
| 功放芯片 | **MAX98357A**（I2S Class-D 单声道功放） |
| 扬声器 | 2011 型，8Ω 腔体喇叭 |
| I2S 总线 | I2S_NUM_1，STD 模式 |
| 采样率 | 16000Hz（默认）/ 24000Hz（TTS 输出） |
| 位深 | 16-bit |
| 声道 | 单声道（支持 WAV 立体声回放） |
| DMA 缓冲 | 8 × 1024 样本 |
| 默认音量 | 85%（范围 0-100） |
| 编解码 | PCM S16LE、Opus（下行解码） |
| 可听阈值 | mean_abs × volume ≥ 16 |
| 任务栈 | 28KB，优先级 7，绑定 APP_CPU_NUM |

### 麦克风（录音）

| 项目 | 规格 |
|------|------|
| 类型 | 板载 PDM 麦克风（ESP32S3 Sense 扩展板） |
| I2S 总线 | I2S_NUM_0，PDM_RX 模式 |
| 采样率 | 16000Hz |
| 位深 | 16-bit |
| 声道 | 单声道 |
| 帧大小 | 320 样本 = 20ms @ 16kHz |
| 编码 | Opus，VOIP 模式，复杂度 0，码率 24kbps |
| 语音增强 | 高通滤波器（α=0.969）+ 5× 增益 |
| VAD | 能量门限 + EMA，触发比 1.3×，底噪阈值 140 |
| 上行批量 | 5 个 Opus 帧/WS 消息 |
| 最大连续上行 | 30 秒/段 |
| 任务栈 | 28KB，优先级 6，绑定 APP_CPU_NUM |

### 音频引脚分配

**扬声器 I2S：**

| 信号 | GPIO | XIAO Pad |
|------|------|----------|
| DIN | 1 | D0 |
| BCLK | 6 | D5 |
| LRC | 5 | D4 |
| SD | NC | 未连接 |
| GAIN | NC | 未连接 |

**麦克风 PDM：**

| 信号 | GPIO |
|------|------|
| CLK | 42 |
| DATA | 41 |

### VAD（语音活动检测）配置

| 参数 | 值 |
|------|-----|
| mode | 3 |
| frame_ms | 30 |
| min_speech_ms | 250 |
| max_silence_ms | 500 |
| pre_speech_ms | 300 |
| silero_threshold | 0.5 |
| silero_threshold_low | 0.2 |

---

## 6. XIAO ESP32S3 Pad-GPIO 映射

```
D0  = GPIO1    D1  = GPIO2    D2  = GPIO3    D3  = GPIO4
D4  = GPIO5    D5  = GPIO6    D6  = GPIO43   D7  = GPIO44
D8  = GPIO7    D9  = GPIO8    D10 = GPIO9
```

---

## 7. 电源要求

| 模块 | 电压 | 备注 |
|------|------|------|
| MCU + 显示屏 + 摄像头 | 3.3V | XIAO 板载稳压，USB 供电 |
| 舵机 | **5V ≥ 1A** | 独立供电，与逻辑共地 |
| MAX98357A 功放 | 3.3V | XIAO 板载供电 |
| USB-C 接口 | 5V | Board1 PCB 上的 TYPE-C 6P（LCSC C668623），CC1/CC2 5.1k 下拉电阻 |

**建议总供电：5V ≥ 1A**（舵机为主要电流消耗）

---

## 8. 通信协议总览

| 协议 | 用途 | 详情 |
|------|------|------|
| SPI | 显示屏驱动 | Mode 3，80MHz |
| I2S (STD) | 扬声器输出 | I2S_NUM_1，16kHz 16-bit 单声道 |
| I2S (PDM_RX) | 麦克风采集 | I2S_NUM_0，16kHz 16-bit PDM |
| SCCB/I2C | 摄像头控制 | SIOD=GPIO40, SIOC=GPIO39 |
| 8-bit 并行 | 摄像头数据 | D0-D7 + VSYNC/HREF/PCLK |
| PWM (MCPWM) | 舵机控制 | 50Hz, 1000-2000μs 脉冲 |
| WiFi | 网络连接 | STA 模式（正常）/ AP 模式（配网） |
| WebSocket | 云端通信 | ws:// 或 wss://，路径 /asr_chat |
| HTTP | 配网门户 | AP 模式下 WebServer 192.168.4.1 |
| USB CDC | 串口调试 | 115200 baud |

### WebSocket 协议细节

- 端点：`ws://<host>:<port>/asr_chat?device_id=<mac>&pin_code=<pin>`
- 最大消息大小：1MB
- TCP 超时：15s，写超时：5s
- 线格式：`u32be(json_len) + json_utf8 + optional_binary`
- 服务端端口：9000
- Ping 间隔：120s，Ping 超时：300s

---

## 9. 半双工音频注意事项

- 扬声器播放时麦克风自动静音（mic_set_speaker_state 门控）
- TTS 结束后 **300ms 尾部抑制**（无 AEC）
- 麦克风无回声消除（AEC），依赖时分复用避免自激

---

## 10. 关键注意事项

### ⚡ 电源
1. **舵机必须独立 5V 供电（≥1A）**，不可直接从 XIAO 板取电，否则会导致 MCU 复位或工作不稳定
2. 舵机电源与逻辑电源必须**共地**
3. USB-C 接口需确保 CC1/CC2 5.1k 下拉电阻焊接正确，否则无法被主机识别

### 🔌 引脚冲突
1. **D6/D7（GPIO43/44）是 UART0**，固件已将舵机从 D6/D7 改为 D3/D9，避免冲突
2. **旧版 PCB（Board1）** 的舵机走线仍连到 D6/D7，需要手动飞线改到 D3/D9
3. 板载 LED 引脚与音频/摄像头冲突，**不可用**

### 📷 摄像头
1. XCLK 必须为 **10MHz**，20MHz 会导致 ESP32-S3 上 DMA 损坏（绿屏）
2. 初始化后需丢弃 **30 帧**预热画面（AWB/AGC 收敛）
3. 120° 广角镜头会产生桶形畸变，服务端可配置去畸变（默认关闭）

### 🔊 音频
1. **无回声消除（AEC）**，采用半双工方案：播放时静音麦克风
2. TTS 结束后有 300ms 静默期，避免尾音被采集
3. Opus 编码复杂度设为 0（最低），优先保证实时性

### 🖥️ 显示屏
1. RST 和 BL 引脚**硬接 3.3V**，固件无法控制复位和背光
2. 横屏旋转后 X 起始位置需偏移 -18（DESKBOT_DISPLAY_ROT3_XSTART_ADJ）
3. 顶部有 4px 安全区，绘制内容应避开

### 📡 通信
1. WebSocket 最大帧 1MB，单帧 JPEG 最大 32KB
2. 下行 PB 帧最大 64KB JSON + 480KB 二进制（10s @ 24kHz PCM）
3. PB 帧间需 50ms 间隔，PB 块间需 150ms 间隔
4. WiFi 默认 SSID：`deskbot_wifi`，密码：`hello2026`

### 🔧 调试
1. USB CDC 串口波特率 115200
2. 配网模式：AP 热点，WebServer 地址 192.168.4.1
3. 固件支持命令行交互（通过串口）
