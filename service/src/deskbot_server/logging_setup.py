import logging
import os
import sys
import time

from deskbot_server.constants import LOG_FILE


class _MillisecondFormatter(logging.Formatter):
    """日志时间戳精确到毫秒（3 位）。"""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        ct = self.converter(record.created)
        base = time.strftime("%Y-%m-%d %H:%M:%S", ct)
        return f"{base}.{int(record.msecs):03d}"


class _WebsocketsServerNoiseFilter(logging.Filter):
    """降级 ``websockets.server`` 上同端口混跑 HTTP 时的无害噪音。

    1) ``opening handshake failed`` + ``EOFError`` / ``InvalidMessage``：TCP 半开、探针、
       把明文端口当 HTTPS、或握手读到一半断开（非协议级配置错误）。
    2) ``connection rejected (204 No Content)``：浏览器对 ``/api/*`` 的 **CORS 预检**
       （OPTIONS），``_build_http_request_handler`` 按设计返回 204；常见于 Flask 调试页
       在 :5050、而 ``fetch`` 指向 ``http(s)://…:9000`` 的跨源场景。
    3) ``connection rejected (200 OK)``：Flask ``/proxy/deskbot`` 等转发到同端口 ``/api/*``
       的普通 HTTP GET/POST；``process_request`` 正常返回 200，非 WebSocket 升级失败。

    以上降级为 DEBUG 并去掉附带 traceback，避免 ``DESKBOT_SERVER_LOG_LEVEL=DEBUG`` 时刷屏。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "websockets.server":
            return True
        try:
            msg = record.getMessage()
        except Exception:
            return True
        if "connection rejected" in msg and ("204" in msg or "200 OK" in msg):
            if record.levelno >= logging.INFO:
                record.levelno = logging.DEBUG
                record.levelname = "DEBUG"
            return True
        if "opening handshake failed" not in msg:
            return True
        exc_info = record.exc_info
        if exc_info and exc_info[0] is not None:
            exc_type = exc_info[0]
            qualname = f"{exc_type.__module__}.{exc_type.__name__}"
            if exc_type is EOFError or qualname.endswith(
                ("websockets.exceptions.InvalidMessage",)
            ):
                record.levelno = logging.DEBUG
                record.levelname = "DEBUG"
                record.exc_info = None
        return True


def setup_logging() -> None:
    log_level = os.environ.get("DESKBOT_SERVER_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, log_level, logging.INFO)
    formatter = _MillisecondFormatter(
        fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)

    root.addHandler(stream_handler)
    root.addHandler(file_handler)

    # 装到 websockets.server logger 上，对 root logger 透明
    logging.getLogger("websockets.server").addFilter(_WebsocketsServerNoiseFilter())
