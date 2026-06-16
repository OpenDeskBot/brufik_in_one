# Security

请勿在公开 Issue 报告漏洞；请私信维护者并说明影响与复现步骤。

## 密钥与敏感文件

- 勿提交 `.env`、用户 API Key、`data/.free_api_key`、`data/.builtin_password`
- `data/opendesk.db` 含用户与 Key 哈希，勿提交生产库
- `data/device/` 含设备 session、记忆等人数据，已加入 `.gitignore`，勿提交

## 网络暴露

- 默认绑定 `0.0.0.0`（`:9000` 设备链路、`:5050` 控制台）：公网暴露前请加防火墙、反向代理与 TLS
- `LLM_API_KEY` 仅通过环境变量 / `.env` 注入，勿写入 `config.yaml` 或日志

## 认证与隔离

- Web 控制台（`:5050`）：邮箱 + 密码注册登录；无默认账号、无邮件找回密码
- 生产请设置随机长字符串 `DESKBOT_WEB_SECRET_KEY`
- 设备连接 `:9000` 须有效 API Key（`?api_key=` 或 `X-API-Key`）
- 免费 Key（`odk_free_`）每日 1GB 总配额；超额拒绝连接
- 设备操作、定时任务、记忆、人脸数据按账号绑定的 `device_id` 隔离

## 自托管建议

- 定期轮换 API Key；限制 `:5050` / `:9000` 仅内网可达
- 备份 `data/opendesk.db` 与 `data/device/`（若需保留用户数据）
