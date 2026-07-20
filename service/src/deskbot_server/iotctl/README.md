# miot-ctl

轻量米家 IoT 命令行工具，基于 [miloco-miot](https://github.com/XiaoMi/xiaomi-miloco) SDK，**不依赖 Miloco 服务端**。

安装体积约 **50–80 MB**（仅 HTTP 控制所需依赖，无 opencv/onnx/感知模型）。

## 快速开始

```bash
# 1. 安装（自动下载 wheel + 创建虚拟环境）
bash install.sh

# 2. 绑定小米账号
./miot-ctl auth bind

# 授权后浏览器会跳到 https://127.0.0.1/?code=...&state=...
# 页面显示「无法访问」是正常的，复制地址栏完整 URL 执行：
./miot-ctl auth authorize "https://127.0.0.1/?code=...&state=..."

# 3. 列出设备
./miot-ctl device list

# 4. 查看设备能力
./miot-ctl device spec <did>

# 5. 控制设备
./miot-ctl device set <did> on true
./miot-ctl device set <did> brightness 60
./miot-ctl device props <did> brightness

# 6. 调用动作（如音箱播报）
./miot-ctl device action <did> play-text "你好"
```

## 目录结构

```
miot-ctl/
├── install.sh          # 一键安装
├── miot-ctl            # 启动脚本
├── requirements.txt    # 最小 Python 依赖
├── miot_ctl/           # 工具源码
├── wheels/             # install.sh 下载的 SDK wheel（可随目录一起拷贝）
├── .venv/              # 虚拟环境（install.sh 创建）
└── data/               # 运行时数据（token、spec 缓存）
    ├── auth.json
    ├── config.json
    └── cache/
```

## 拷贝到其他机器

整个 `miot-ctl/` 目录可以直接复制走。到新机器后：

```bash
cd miot-ctl
bash install.sh    # 若已带 wheels/ 和 .venv 可跳过，直接 ./miot-ctl
./miot-ctl auth status
```

建议一并拷贝 `data/`（保留登录态）或在新机器重新 `auth bind`。

也可设置数据目录：

```bash
export MIOT_CTL_HOME=/path/to/your/data
./miot-ctl device list
```

## 命令参考

### 账号

| 命令 | 说明 |
|------|------|
| `auth bind` | 浏览器授权绑定 |
| `auth authorize <base64>` | 非交互提交授权码 |
| `auth status` | 查看绑定状态 |
| `auth unbind` | 解绑 |

### 设备

| 命令 | 说明 |
|------|------|
| `device list [--online] [--json]` | 设备列表 |
| `device get <did>` | 设备详情 |
| `device spec <did>` | 能力列表 |
| `device set <did> <key> <value>` | 写属性 |
| `device props <did> [key...]` | 读属性 |
| `device action <did> <key> [args...]` | 调用动作 |

`key` 支持：
- `type_name`：如 `on`、`brightness`、`color-temperature`
- `prop.{siid}.{piid}` / `action.{siid}.{aiid}`

`value` 自动推断类型：`true/false/on/off` → 布尔，纯数字 → 数值，其余 → 字符串。

### 场景

| 命令 | 说明 |
|------|------|
| `scene list` | 列出手动场景 |
| `scene run <scene_id>` | 触发场景 |

## 手动安装 wheel

若 `install.sh` 下载失败，可手动放置 wheel：

```bash
# 从 GitHub Release 下载对应平台 wheel，例如：
# miloco_miot-2026.7.3-py3-none-manylinux_2_28_x86_64.whl
mkdir -p wheels
cp /path/to/miloco_miot-*.whl wheels/
bash install.sh
```

## 许可

基于 Xiaomi Miloco / miloco-miot SDK，**仅限非商业用途**。详见上游 [LICENSE.md](https://github.com/XiaoMi/xiaomi-miloco/blob/main/LICENSE.md)。

## 故障排查

| 现象 | 处理 |
|------|------|
| `未绑定小米账号` | 运行 `auth bind` |
| `state 不匹配` | 重新 `auth bind`，不要复用旧授权码 |
| `设备 spec 中未找到` | 先 `device spec <did>` 查正确 key |
| 控制成功但设备无反应 | 看返回 JSON 中 `code` 负值和 `code_msg` |
| 安装慢 | 设置 `export UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple` |
