# 联调工具

| 脚本 | 说明 |
|------|------|
| [test_phoneme_client.py](./test_phoneme_client.py) | 连接 `streaming_phoneme`，打印分片摘要并可导出 WAV |

前置：仓库根目录已 `./start.sh`（或本目录 `./start-local.sh`），且 `.venv` 已安装依赖。

```bash
cd paddlespeech-server
source .venv/bin/activate
python tools/test_phoneme_client.py --text "测试一下"
```

主服务全链路测试见 [../../deskbot-server/tools/README.md](../../deskbot-server/tools/README.md)。
