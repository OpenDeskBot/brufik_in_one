"""线程安全单例元类（Python 常用实现）。"""

from __future__ import annotations

import threading
from typing import Any


class SingletonMeta(type):
    """``class Foo(metaclass=SingletonMeta)``：全局唯一实例。

    首次构造后忽略后续 ``__init__`` 参数；需要重配时在实例上提供 ``configure`` / ``bind``。
    """

    _instances: dict[type, Any] = {}
    # RLock：构造 A 时若 __init__ 再构造其它 Singleton（如 UserService→UserDao）可重入
    _lock = threading.RLock()

    def __call__(cls, *args: Any, **kwargs: Any) -> Any:
        if cls not in cls._instances:
            with cls._lock:
                if cls not in cls._instances:
                    cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]

    def reset_instance(cls) -> None:
        """测试用：清除单例缓存。"""
        with cls._lock:
            cls._instances.pop(cls, None)
