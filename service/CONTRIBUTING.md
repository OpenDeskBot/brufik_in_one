# Contributing

## 本地环境

1. `cp .env.example .env`，填写 `LLM_API_KEY`
2. 在 `service/` 目录执行 `./start.sh`（或已装依赖时 `SKIP_SETUP=1 ./start.sh`）
3. 浏览器打开 `http://127.0.0.1:5050/` 注册账号，用 `data/.free_api_key` 或控制台 Key 联调

## 提交前检查

```bash
source .venv/bin/activate
ruff check src
pytest tests/ -q
```

开发依赖：`pip install -e ".[dev]"`。

## 文档

改协议、配置、控制台功能或 LLM 工具时，请同步更新：

- [README.md](README.md)
- [docs/SERVER.md](docs/SERVER.md)
- [docs/esp32_pb_protocol.md](docs/esp32_pb_protocol.md)（若改设备协议）
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)（若改模块结构）

勿提交 `.env`、模型权重、`data/device/` 运行时数据。

贡献采用 [GPL-3.0](LICENSE)。
