from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from deskbot_server.db.engine import get_session
from deskbot_server.db.models import User

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def validate_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(normalize_email(email)))


def get_user_by_email(email: str) -> User | None:
    session = get_session()
    return session.scalar(select(User).where(User.email == normalize_email(email)))


def get_user_by_id(user_id: str) -> User | None:
    session = get_session()
    return session.get(User, user_id)


def create_user(email: str, password: str) -> User:
    email_norm = normalize_email(email)
    if not validate_email(email_norm):
        raise ValueError("邮箱格式无效")
    if len(password) < 8:
        raise ValueError("密码至少 8 位")

    session = get_session()
    user = User(
        email=email_norm,
        password_hash=generate_password_hash(password),
        is_active=True,
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ValueError("该邮箱已注册") from exc
    session.refresh(user)
    session.expunge(user)
    return user


def verify_password(user: User, password: str) -> bool:
    return check_password_hash(user.password_hash, password)


def update_display_name(user_id: str, display_name: str) -> None:
    name = (display_name or "").strip()[:64]
    if not name:
        raise ValueError("用户名称不能为空")
    session = get_session()
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise ValueError("用户不存在")
    user.display_name = name
    session.commit()


def change_password(user_id: str, old_password: str, new_password: str) -> None:
    if len(new_password) < 8:
        raise ValueError("新密码至少 8 位")
    session = get_session()
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise ValueError("用户不存在")
    if not check_password_hash(user.password_hash, old_password):
        raise ValueError("旧密码错误")
    user.password_hash = generate_password_hash(new_password)
    session.commit()
