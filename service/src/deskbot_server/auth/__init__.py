"""身份与鉴权适配层（Flask 登录 / API Key / 调试 WS Token）。

数据访问请优先用 ``dao.UserDao`` / ``dao.DeviceDao`` / ``dao.ApiKeyDao``；
本包保留面向 Web 与 WS 网关的函数式 API。
"""
