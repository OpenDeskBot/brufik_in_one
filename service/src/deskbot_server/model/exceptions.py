"""共享异常类：跨层使用的业务异常定义。"""


class QuotaExceededError(Exception):
    """API 配额超限。"""
