# Board1 原理图（重建版）

本目录提供 **Brufik 转接板 Board1 / PCB1** 的连接关系重建文件。  
原始 PCB 在嘉立创 EDA 中设计（BOM 导出者 `jlceda`，2026-06-12），但仓库目前只包含 BOM 与贴片坐标，**未包含原始 `.epro` 工程**。

## 文件说明

| 文件 | 用途 |
|------|------|
| **`Board1_kicad.zip`** | **可直接导入嘉立创 EDA 的 KiCad 工程压缩包（推荐）** |
| `Board1.kicad_pro` / `.kicad_sch` / `.kicad_pcb` | KiCad 工程（**v2.1 PCB 已含元件布局**） |
| `generate_kicad.py` | 重新生成上述 KiCad 文件的脚本 |
| `Board1_schematic.svg` | 可视化原理图/框图，可导入嘉立创 EDA 作参考底图 |
| `Board1_pinout.csv` | 逐引脚网表，手工画原理图时对照 |
| `Board1.net` | KiCad 风格网表，可用于核对网络 |
| `../BOM_Board1_PCB1_2026-06-12.xlsx` | 官方 BOM（LCSC 料号/封装） |
| `../PickAndPlace_PCB1_2026_06_12.xlsx` | 官方贴片坐标 |

## 导入嘉立创 EDA 的推荐步骤

### 方案 A：你有原始嘉立创账号工程（最佳）

若 PCB 是你或团队在嘉立创 EDA 里画的：

1. 登录 [嘉立创EDA 标准版](https://lceda.cn/editor) 或 [专业版](https://pro.lceda.cn/editor)
2. 打开对应工程 → **个人中心 → 工程 → 工程高级设置 → 下载工程**
3. 得到 `.epro` 或 JSON 压缩包，直接在编辑器中打开即可

> BOM 里的 `docProps/custom.xml` 含工程指纹，说明原始文件来自嘉立创 EDA。

### 方案 B：在嘉立创 EDA 中按 BOM 重建原理图（本仓库无原工程时）

转接板元件少，建议 **30–60 分钟手工重建**，比格式转换更可靠。

#### 1. 新建工程

- 标准版：<https://lceda.cn/editor> → 新建 → 原理图 + PCB
- 专业版：<https://pro.lceda.cn/editor> → 新建工程

#### 2. 放置元件（与 BOM 一致）

在 LCSC 库中搜索并放置：

| 位号 | 型号/关键词 | 封装 |
|------|-------------|------|
| USB1 | `TYPE-C 6P(073)` / C668623 | TYPE-C-SMD_TYPE-C-6P-073 |
| 1K1, 5.1k | `0805 5.1k` / C27834 | R0805 |
| H3, H4 | `PZ254V-11-07P` / C492406 | HDR-TH_7P-P2.54-V-M |
| vin | `PZ254R-11-07P` / C492415 | HDR-TH_7P-P2.54-H-M-W10.4 |
| vcc | `ZX-MX1.25-8PLT` / C19272385 | CONN-SMD_8P-P1.25 |
| X舵机 | `PZ254R-11-03P` / C492411 | HDR-TH_3P-P2.54-H-M-W10.4 |
| Y舵机 | `PZ254V-11-03P` / C2937625 | HDR-TH_3P-P2.54-V-M |

也可：**文件 → 导入 → BOM**，直接导入 `BOM_Board1_PCB1_2026-06-12.xlsx`（若编辑器支持），再补连线。

#### 3. 按网表连线

打开 `Board1_pinout.csv`，逐网络连接。核心网络：

```
+5V:  USB1.VBUS → H4-5V → X舵机-2 → Y舵机-2
GND:  所有连接器 GND、CC 下拉电阻另一端
+3V3: H4-3V3 → vin-1 → vcc-1
I2S:  H3-D0/D5/D4 → vin-3/4/5
LCD:  H3-D1/D2 + H4-D8/D10 → vcc-5/6/4/3
舵机: H4-D6/D7 → Y舵机-3 / X舵机-3
喇叭: vcc-7/8 → vin-6/7 → MAX98357 模块
USB CC: CC1→1K1→GND, CC2→5.1k→GND
```

#### 4. 导入参考底图（可选）

- **标准版**：PCB 编辑器 → **工具 → 导入图像** → 选 `Board1_schematic.svg`，缩小透明度作对照
- **专业版**：PCB → **放置 → 图片** 或导入 DXF/SVG 参考层

#### 5. 原理图转 PCB

- 菜单：**设计 → 原理图转 PCB**
- 检查封装映射无误后生成 PCB

#### 6. 对齐已有贴片坐标

若需与官方 PCB 布局一致：

1. PCB 中 **文件 → 导入 → 坐标文件**（或手动对照）
2. 导入 `PickAndPlace_PCB1_2026_06_12.xlsx`
3. 逐位号核对 Mid X/Y、Rotation、Layer

#### 7. 设计规则检查

- 原理图：**设计 → 电气规则检查 (ERC)**
- PCB：**设计 → 设计规则检查 (DRC)**
- 对照 `Board1_pinout.csv` 与 `firmware/deskbot_config.h` 引脚定义

### 方案 C：导入本目录 KiCad 工程（最快）

已生成完整 KiCad 工程，可直接导入嘉立创 EDA：

#### 嘉立创 EDA 标准版

1. 打开 <https://lceda.cn/editor>
2. **文件 → 导入 → KiCad**
3. 选择 `Board1_kicad.zip`（或整个 `Board1/` 文件夹打 ZIP）
4. 等待解析完成，打开原理图核对网络
5. 打开 **PCB 编辑器** — v2.1 已按官方 PickAndPlace 放置 9 个元件（约 36×44mm 板框）
6. 若 PCB 仍为空，重新导入最新 `Board1_kicad.zip`（勿用旧版）
7. **设计 → 更新 PCB** 同步原理图网络后 **自行布线**（无官方走线）

#### 嘉立创 EDA 专业版

1. 打开 <https://pro.lceda.cn/editor>
2. **文件 → 导入 → KiCad**（或使用 **格式转换器**）
3. 选择 `Board1_kicad.zip`
4. 核对符号/网络，打开 PCB 查看元件布局（9 个封装 + 板框）
5. **更新 PCB** 同步网络，再布线

#### 用 KiCad 打开（可选）

1. 安装 [KiCad 8+](https://www.kicad.org/download/)
2. 打开 `Board1.kicad_pro`
3. 若提示库缺失，可忽略（符号已内嵌在 `.kicad_sch`）
4. 修改后 **文件 → 归档工程** 再导入嘉立创

> 本 KiCad 工程为 **BOM + 固件引脚反推重建**，非官方 Gerber 源文件。导入后请对照 `Board1_pinout.csv` 做 ERC，并将连接器封装替换为 LCSC 官方 footprint（C668623 等）。

## XIAO 插座引脚对照

```
H3 (7P):  D0  D1  D2  D3  D4  D5  GND
H4 (7P):  5V  3V3 D6  D7  D8  D9  D10
```

丝印与 GPIO 对应见 `firmware/deskbot_config.h`：

- D0=GPIO1 (I2S DIN)
- D1=GPIO2 (LCD CS)
- D2=GPIO3 (LCD DC)
- D4=GPIO5 (I2S LRC)
- D5=GPIO6 (I2S BCLK)
- D6=GPIO43 (Y 舵机)
- D7=GPIO44 (X 舵机)
- D8=GPIO7 (LCD SCK)
- D10=GPIO9 (LCD MOSI)

## 注意事项

1. **SPK+/SPK- 引脚顺序**：本重建依据说明书「头部喇叭线 → 转接板 → 功放板」走线推断；若你手上有已装好的板子，用万用表通断档核对 vcc-7/8 与 vin-6/7 最稳妥。
2. **D3、D9** 在本板未引出到外部连接器，原理图中可悬空或加 NC 标记。
3. 若需下单 PCB，可直接使用官方 Gerber（如后续仓库补充）；本重建版仅供原理图编辑/学习，**不保证与量产 Gerber 逐线一致**。

## 相关文档

- 接线表：[`README_zh.md`](../../../README_zh.md) 第三节
- 组装说明：[`mechanical/说明书1.02PDF.pdf`](../../说明书1.02PDF.pdf)
