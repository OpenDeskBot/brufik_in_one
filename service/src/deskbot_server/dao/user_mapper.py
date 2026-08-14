"""用户表 SQL Mapper — MyBatis 注解风格。

对比原来 user_dao.py 的写法：

    # 原来 (user_dao.py)
    def get_by_email(self, email: str) -> User | None:
        session = get_session()
        return session.scalar(select(User).where(User.email == self.normalize_email(email)))

    # 现在 (user_mapper.py)
    @sql_scalar("SELECT * FROM users WHERE email = :email AND is_active = 1", model=User)
    def get_by_email(email: str) -> User | None: ...

SQL 写在装饰器里，参数绑定自动从函数签名提取，session 管理完全由框架处理。
"""

from __future__ import annotations

from deskbot_server.db.models import User
from deskbot_server.db.sql_decorators import execute, select, select_one

# ────────────────────────── 查询 ──────────────────────────


@select_one("SELECT * FROM users WHERE email = :email AND is_active = 1", model=User)
def get_by_email(email: str) -> User | None:
    """根据邮箱查找活跃用户。"""


@select_one("SELECT * FROM users WHERE id = :user_id AND is_active = 1", model=User)
def get_by_id(user_id: str) -> User | None:
    """根据 ID 查找活跃用户。"""


@select("SELECT * FROM users ORDER BY created_at ASC", model=User)
def list_all() -> list[User]:
    """列出所有用户。"""


@select_one("SELECT COUNT(*) FROM users WHERE is_developer = :is_dev AND is_active = 1")
def count_developers(is_dev: bool = True) -> int:
    """统计开发者数量。"""


@select_one("SELECT id FROM users LIMIT 1")
def has_any_user() -> str | None:
    """返回第一个用户 ID，无用户时返回 None。"""


# ────────────────────────── 写操作 ──────────────────────────


@execute(
    """
    INSERT INTO users (id, email, password_hash, is_developer, is_active, created_at, updated_at)
    VALUES (:uid, :email, :pw_hash, :is_dev, 1, datetime('now'), datetime('now'))
    """,
    model=User,
)
def create(uid: str, email: str, pw_hash: str, is_dev: bool) -> User:
    """创建用户（配合 RETURNING 可返回 User 对象，此处无 RETURNING 时返回 None）。"""


@execute("UPDATE users SET display_name = :name WHERE id = :uid AND is_active = 1")
def update_display_name(uid: str, name: str) -> int:
    """更新用户昵称，返回影响行数。"""


@execute("UPDATE users SET is_developer = :is_dev WHERE id = :uid AND is_active = 1")
def set_developer(uid: str, is_dev: bool) -> int:
    """设置/取消开发者权限。"""


@execute("UPDATE users SET password_hash = :pw_hash WHERE id = :uid AND is_active = 1")
def update_password(uid: str, pw_hash: str) -> int:
    """更新密码哈希。"""


# ────────────────────────── 复杂查询示例 ──────────────────────────


@select(
    """
    SELECT u.*
    FROM users u
    JOIN devices d ON d.owner_user_id = u.id
    WHERE d.device_id = :device_id AND u.is_active = 1
    """,
    model=User,
)
def get_by_device_id(device_id: str) -> User | None:
    """根据设备 ID 查找设备所属用户（JOIN 查询）。"""
