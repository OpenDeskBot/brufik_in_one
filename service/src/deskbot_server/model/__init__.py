"""数据模型层：纯数据定义（dataclass / Enum / 异常），零业务逻辑、零 IO。"""

from deskbot_server.model.chat import ChatTurnResult
from deskbot_server.model.exceptions import QuotaExceededError
from deskbot_server.model.settings import AppSettings

__all__ = ["AppSettings", "ChatTurnResult", "QuotaExceededError"]
