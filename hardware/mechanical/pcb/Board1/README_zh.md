# Board1 转接板（PCB1）说明

本目录是 **Brufik 转接板 Board1 / PCB1** 的接线文档与重建文件。
原始 PCB 在嘉立创 EDA 中设计（BOM 导出者 `jlceda`，2026-06-12），仓库包含 BOM、贴片坐标
与重建的 KiCad 工程。以下内容已按**原版嘉立创工程（PCB 丝印 + 铜箔）逐线核对**，
并和固件 `firmware/deskbot_config.h` 交叉验证，可放心照着接线/画板。

## 文件说明

| 文件 | 用途 |
|------|------|
| **`Board1_kicad.zip`** | 可直接导入嘉立创 EDA 的 KiCad 工程压缩包（推荐） |
| `Board1.kicad_pro` / `.kicad_sch` / `.kicad_pcb` | KiCad 工程（PCB 已含元件布局） |
| `generate_kicad.py` | 重新生成上述 KiCad 文件的脚本 |
| `Board1_pcb_layout_real.svg` | **真实 PCB 版图连线作用图**（按原版工程坐标绘制：红=顶层铜、蓝=底层铜、金=焊盘、黄=丝印，叠加彩色网络示意线） |
| `Board1_schematic_real.svg` | **原版原理图几何还原图**（元件/引脚/导线/网络名） |
| `Board1_schematic.svg` | 可视化原理图/框图，可导入嘉立创 EDA 作参考底图 |
| `Board1_tscircuit.html` | **交互式 PCB 模型 + 连线说明页**（双击 `打开Board1连线图.py` 查看） |
| `Board1_circuit.tsx` | 上述交互模型的 tscircuit 源码 |
| `打开Board1连线图.py` | 本地 HTTP 启动器（tscircuit 3D 视图需要本地服务） |
| `Board1_pinout.csv` | 逐引脚网表（UTF-8 BOM，Excel 打开不乱码） |
| `Board1.net` | KiCad 风格网表，可用于核对网络 |
| `../BOM_Board1_PCB1_2026-06-12.xlsx` | 官方 BOM（LCSC 料号/封装） |
| `../PickAndPlace_PCB1_2026_06_12.xlsx` | 官方贴片坐标 |

## 接线总表（已核对）

> 排针命名与板面丝印一致：**H3 = 右排（靠 USB-C/X 舵机），H4 = 左排（靠 Y 舵机）**。
> 原理图符号名 `ESP32S3_H1/H2` 对应关系：H1 ↔ H4、H2 ↔ H3。

```
5V   : USB1.VBUS(A9/B9) → X舵机-2 → Y舵机-2 → vin-1(VIN) → H3-1(USB)
GND  : USB1.GND(A12/B12) → 全部 GND（X/Y舵机-1、vin-2、vcc-2、H3-2）
3V3  : H3-3(3V3) → vcc-1（头部屏幕逻辑电源）
屏幕 : vcc-3 LCD_DIN ← H3-4(D10/MOSI)
       vcc-4 LCD_CLK ← H3-6(D8/SCK)
       vcc-5 LCD_CS  ← H4-2(D1)
       vcc-6 LCD_DC  ← H4-3(D2)
功放 : vin-5 DIN ← H4-1(D0)
       vin-6 BCLK ← H4-6(D5)
       vin-7 LRC  ← H4-5(D4)
       vin-1 VIN = 5V，vin-2 GND
舵机 : X舵机-3 SX ← H3-5(D9/GPIO8)；Y舵机-3 SY ← H4-4(D3/GPIO4)
       3P 座脚序 1=GND(黑)、2=+5V(红)、3=SIG(黄/白)
未接 : H4-7(D6)、H3-7(D7)、MAX98357 SD/GAIN、vcc-7/8/9/10
```

### 重要说明

1. **XIAO 排针**：H4 = D0 D1 D2 D3 D4 D5 D6（第 7 脚是 D6，不是 GND）；H3 = USB GND 3V3 D10 D9 D8 D7
   （第 1 脚是 USB/5V，3V3 在第 3 脚）。**D6/D7 悬空**，不是舵机脚。
2. **MAX98357 模块（vin 7P 排针）**：脚序 **1=VIN(5V)、2=GND、3=SD、4=GAIN、5=DIN、6=BCLK、7=LRC**，
   **没有 SPK+/SPK-**。VIN 是 5V 不是 3.3V；喇叭只接模块自带的**绿色 2P 接线端子**，转接板不经过喇叭信号。
3. **舵机**：X舵机=D9/GPIO8（SX，左右），Y舵机=D3/GPIO4（SY，上下），与固件
   `DESKBOT_ROM_X_PIN=8`、`DESKBOT_ROM_Y_PIN=4` 一致。3P 座脚序是标准的
   **1=GND(黑)、2=+5V(红)、3=SIG(黄/白)**。
4. **USB CC**：PCB 上有两颗 0805 下拉电阻，位号 `5.1k` 和 `1K1`，**阻值同为 5.1kΩ**
   （`1K1` 是位号不是 1.1kΩ，LCSC C27834）。CC1→`5.1k`，CC2→`1K1`。
5. **USB-C**：纯供电（6P：GND×2、5V×2、CC1、CC2），无 D+/D-。
6. PCB 共 9 个元件：`USB1、vcc、vin、H3、H4、X舵机、Y舵机、5.1k、1K1`。

## 使用方法

### 方案 A：有原始嘉立创账号工程（最佳）

直接在嘉立创 EDA（标准版/专业版）打开原工程即可，无需本目录重建文件。

### 方案 B：从 BOM + 贴片坐标重建（了解原理）

1. 对照 `../BOM_Board1_PCB1_2026-06-12.xlsx` 放置 9 个元件
2. 原理图按上文「接线总表」连接网络
3. 打开 **PCB 编辑器**，按 `../PickAndPlace_PCB1_2026_06_12.xlsx` 对齐位号
4. 设计规则检查（ERC/DRC）后自行布线（本仓库重建版无官方走线）

### 方案 C：导入本目录 KiCad 工程（最快）

1. 打开 <https://lceda.cn/editor>（标准版）或 <https://pro.lceda.cn/editor>（专业版）
2. **文件 → 导入 → KiCad**，选择 `Board1_kicad.zip`
3. 等待解析完成，打开原理图核对网络
4. 打开 **PCB 编辑器**（已按官方 PickAndPlace 放置 9 个元件）
5. **设计 → 更新 PCB** 同步网络后自行布线

> 本 KiCad 工程为 **BOM + 固件引脚重建**，非官方 Gerber 源文件。导入后请对照
> `Board1_pinout.csv` 做 ERC，并将连接器封装替换为 LCSC 官方 footprint（C668623 等）。

## XIAO 插座引脚对照

```
H4 (7P 左排): D0   D1   D2   D3   D4   D5   D6
H3 (7P 右排): USB  GND  3V3  D10  D9   D8   D7
```

GPIO 对应（见 `firmware/deskbot_config.h`）：

- D0=GPIO1 (I2S DIN → MAX98357)
- D1=GPIO2 (LCD CS)
- D2=GPIO3 (LCD DC)
- D3=GPIO4 (Y 舵机，上下/俯仰)
- D4=GPIO5 (I2S LRC → MAX98357)
- D5=GPIO6 (I2S BCLK → MAX98357)
- D6=GPIO43 (悬空)
- D7=GPIO44 (悬空)
- D8=GPIO7 (LCD SCK)
- D9=GPIO8 (X 舵机，左右/水平)
- D10=GPIO9 (LCD MOSI)

## tscircuit 交互模型

`Board1_tscircuit.html` 是同一块板的交互式模型（真实板框 35.34×45.21 mm、真实封装/旋转、
23 条网络示意），双击 `打开Board1连线图.py` 起本地服务后自动打开浏览器。
`Board1_circuit.tsx` 是其 tscircuit 源码，也可用 [tscircuit](https://tscircuit.com) 在线查看/导出。

## 注意事项

1. 本目录的连线图/网表按原版工程 PCB 丝印 + 铜箔核对，装板/接线前仍建议用万用表通断档复核一次。
2. 若需下单 PCB，优先使用官方 Gerber；本重建版 KiCad 工程仅供原理图编辑/学习，**不保证与量产 Gerber 逐线一致**。

## 相关文档

- 接线表：[`README_zh.md`](../../../README_zh.md) 第三节
- 组装说明：[`mechanical/说明书1.02PDF.pdf`](../../说明书1.02PDF.pdf)
