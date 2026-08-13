"""MyBatis 风格的 SQL 注解装饰器。

用法示例::

    from deskbot_server.db.sql_decorators import sql_query, sql_scalar, sql_update
    from deskbot_server.db.models import User

    # 查询多行 → 返回 list[User]
    @sql_query("SELECT * FROM users WHERE is_active = :active", model=User)
    def list_active_users(active: bool = True) -> list[User]: ...

    # 查询单行 → 返回 User | None
    @sql_scalar("SELECT * FROM users WHERE email = :email", model=User)
    def get_by_email(email: str) -> User | None: ...

    # 查询单个标量值 → 返回 int
    @sql_scalar("SELECT COUNT(*) FROM users WHERE is_developer = :is_dev")
    def count_devs(is_dev: bool = True) -> int: ...

    # 写操作（INSERT / UPDATE / DELETE）→ 返回影响行数
    @sql_update("UPDATE users SET display_name = :name WHERE id = :uid")
    def set_display_name(uid: str, name: str) -> int: ...

    # 返回 dict 列表（无 ORM Model 映射时）
    @sql_query("SELECT device_id, COUNT(*) as cnt FROM devices GROUP BY device_id")
    def device_stats() -> list[dict]: ...

设计原则:
- 函数签名中的参数名即 SQL 中的 :param 绑定名（一一对应）
- model= 指定时自动映射为 ORM 对象；不指定时返回 dict
- SELECT 类自动不提交；写操作自动 commit
- ORM 对象由 raw SQL 手动构造，datetime/date 列自动从字符串转换
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from datetime import date, datetime
from typing import Any, TypeVar

from sqlalchemy import text
from sqlalchemy.orm import Session

from deskbot_server.db.engine import get_session

T = TypeVar("T")


def _bind_params(sql: str, func: Callable, args: tuple, kwargs: dict) -> dict[str, Any]:
    """从函数签名和调用参数中提取 SQL 绑定参数。"""
    sig = inspect.signature(func)
    bound = sig.bind(*args, **kwargs)
    bound.apply_defaults()
    params = {}
    for name, value in bound.arguments.items():
        if name in ("self", "cls"):
            continue
        params[name] = value
    return params


def _coerce_value(value: Any, col_type: type) -> Any:
    """将 SQLite 返回的字符串值转换为 Python 类型。"""
    if value is None or not isinstance(value, str):
        return value
    if col_type is datetime:
        try:
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return value
    if col_type is date:
        try:
            return date.fromisoformat(value)
        except (ValueError, TypeError):
            return value
    return value


def _map_row(row, model: type | None):
    """将结果行映射为 ORM 对象或 dict。"""
    if model is not None:
        obj = model()
        # 预取模型列的 Python 类型，用于 SQLite 字符串 → datetime/date 转换
        col_types: dict[str, type] = {}
        for mapper_col in model.__table__.columns:
            col_types[mapper_col.name] = mapper_col.type.python_type
        for col_name in row._mapping:
            val = row._mapping[col_name]
            expected_type = col_types.get(col_name)
            if expected_type:
                val = _coerce_value(val, expected_type)
            setattr(obj, col_name, val)
        return obj
    return dict(row._mapping)


# ---------------------------------------------------------------------------
# sql_query: SELECT → 返回 list
# ---------------------------------------------------------------------------


def sql_query(sql: str, model: type | None = None):
    """SELECT 查询装饰器，返回 list[model] 或 list[dict]。"""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            params = _bind_params(sql, func, args, kwargs)
            session: Session = get_session()
            result = session.execute(text(sql), params)
            rows = result.fetchall()
            return [_map_row(row, model) for row in rows]

        wrapper._sql_annotation = sql
        wrapper._sql_kind = "query"
        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# sql_scalar: SELECT → 返回单行或单值
# ---------------------------------------------------------------------------


def sql_scalar(sql: str, model: type | None = None):
    """单行/单值查询装饰器。

    - 有 model → 返回 model | None
    - 无 model → 返回标量值（如 COUNT、MAX 等聚合结果）
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            params = _bind_params(sql, func, args, kwargs)
            session: Session = get_session()
            result = session.execute(text(sql), params)
            row = result.fetchone()
            if row is None:
                return None
            if model is not None:
                return _map_row(row, model)
            # 标量：单列时直接返回值，多列时返回 dict
            if len(row._mapping) == 1:
                return row[0]
            return dict(row._mapping)

        wrapper._sql_annotation = sql
        wrapper._sql_kind = "scalar"
        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# sql_update: INSERT / UPDATE / DELETE → 返回影响行数
# ---------------------------------------------------------------------------


def sql_update(sql: str, model: type | None = None):
    """写操作装饰器，自动 commit。返回 int（受影响行数）。"""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            params = _bind_params(sql, func, args, kwargs)
            session: Session = get_session()
            result = session.execute(text(sql), params)
            session.commit()
            return result.rowcount

        wrapper._sql_annotation = sql
        wrapper._sql_kind = "update"
        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# 便捷别名（更贴近 MyBatis 命名）
# ---------------------------------------------------------------------------

select = sql_query
select_one = sql_scalar
execute = sql_update
