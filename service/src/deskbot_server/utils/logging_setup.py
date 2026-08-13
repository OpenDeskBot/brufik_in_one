import logging
import os
import sys
import time


class _MillisecondFormatter(logging.Formatter):
    """日志时间戳精确到毫秒（3 位）。"""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        ct = self.converter(record.created)
        base = time.strftime("%Y-%m-%d %H:%M:%S", ct)
        return f"{base}.{int(record.msecs):03d}"


def setup_logging(log_file: str | None = None) -> None:
    """初始化日志：控制台 + 可选文件。

    Args:
        log_file: 日志文件路径。传 None 时从环境变量 ``DESKBOT_SERVER_LOG_FILE`` 读取。
    """
    level_name = (os.environ.get("DESKBOT_SERVER_LOG_LEVEL") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    fmt = _MillisecondFormatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    resolved = (os.environ.get("DESKBOT_SERVER_LOG_FILE") or log_file or "").strip()
    if resolved:
        fh = logging.FileHandler(resolved, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
