## Summary

<!-- 简要说明本 PR 做了什么、为什么 -->

## Test plan

- [ ] `cd deskbot-server && source .venv/bin/activate && ruff check src && pytest tests/ -q`
- [ ] `./start.sh` 或 `SKIP_SETUP=1 FAST_START=1 ./start.sh` 本地冒烟
- [ ] （若改协议 / 控制台 / LLM 工具）已更新 `README.md`、`deskbot-server/README.md`、`docs/` 或 `ARCHITECTURE.md`
