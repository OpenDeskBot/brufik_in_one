"""用户与设备绑定服务：注册/登录、资料、设备列表与绑定。"""

from __future__ import annotations

import re

from werkzeug.security import check_password_hash, generate_password_hash

from deskbot_server.dao import device_mapper, user_mapper
from deskbot_server.db.models import Device, User, _new_id
from deskbot_server.utils.singleton import SingletonMeta
from deskbot_server.ws.device_pin import is_device_online

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DEVICE_ID_RE = re.compile(r"^[a-zA-Z0-9_.\-]{1,128}$")


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _validate_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(_normalize_email(email)))


def _normalize_device_id(device_id: str) -> str:
    return (device_id or "").strip()


def _validate_device_id(device_id: str) -> bool:
    return bool(_DEVICE_ID_RE.match(_normalize_device_id(device_id)))


class UserService(metaclass=SingletonMeta):
    # ---- 用户（直接调 mapper）----

    def normalize_email(self, email: str) -> str:
        return _normalize_email(email)

    def validate_email(self, email: str) -> bool:
        return _validate_email(email)

    def register(self, email: str, password: str) -> User:
        email_norm = _normalize_email(email)
        if not _validate_email(email_norm):
            raise ValueError("邮箱格式无效")
        if len(password) < 8:
            raise ValueError("密码至少 8 位")
        is_first_user = not user_mapper.has_any_user()
        try:
            user = user_mapper.create(_new_id(), email_norm, generate_password_hash(password), is_first_user)
        except Exception as exc:
            err = str(exc).lower()
            if "email" in err or "unique" in err:
                raise ValueError("该邮箱已注册") from exc
            raise ValueError("注册失败，请稍后重试") from exc
        return user

    def login(self, email: str, password: str) -> User:
        user = user_mapper.get_by_email(_normalize_email(email))
        if user is None or not user.is_active or not check_password_hash(user.password_hash, password):
            raise ValueError("邮箱或密码错误")
        return user

    def get_user(self, user_id: str) -> User | None:
        return user_mapper.get_by_id(user_id)

    def get_user_by_email(self, email: str) -> User | None:
        return user_mapper.get_by_email(_normalize_email(email))

    def user_info(self, user_id: str) -> dict:
        user = user_mapper.get_by_id(user_id)
        if user is None:
            raise ValueError("用户不存在")
        return {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name or "",
            "is_developer": bool(user.is_developer),
        }

    def update_display_name(self, user_id: str, display_name: str) -> None:
        name = (display_name or "").strip()[:64]
        if not name:
            raise ValueError("用户名称不能为空")
        if user_mapper.get_by_id(user_id) is None:
            raise ValueError("用户不存在")
        user_mapper.update_display_name(user_id, name)

    def change_password(self, user_id: str, old_password: str, new_password: str) -> None:
        if len(new_password) < 8:
            raise ValueError("新密码至少 8 位")
        user = user_mapper.get_by_id(user_id)
        if user is None:
            raise ValueError("用户不存在")
        if not check_password_hash(user.password_hash, old_password):
            raise ValueError("旧密码错误")
        user_mapper.update_password(user_id, generate_password_hash(new_password))

    def verify_password(self, user: User, password: str) -> bool:
        return check_password_hash(user.password_hash, password)

    def list_users(self) -> list[User]:
        return user_mapper.list_all()

    def count_developers(self) -> int:
        return user_mapper.count_developers(is_dev=True)

    def set_developer(self, user_id: str, *, is_developer: bool) -> User:
        user = user_mapper.get_by_id(user_id)
        if user is None:
            raise ValueError("用户不存在")
        if user.is_developer and not is_developer and self.count_developers() <= 1:
            raise ValueError("至少保留一名开发者")
        user_mapper.set_developer(user_id, is_developer)
        user.is_developer = is_developer
        return user

    # ---- 设备（直接调 mapper）----

    def normalize_device_id(self, device_id: str) -> str:
        return _normalize_device_id(device_id)

    def validate_device_id(self, device_id: str) -> bool:
        return _validate_device_id(device_id)

    def list_devices(self, user_id: str) -> list[Device]:
        return device_mapper.list_for_user(user_id)

    def get_device(self, device_id: str) -> Device | None:
        return device_mapper.get_by_device_id(_normalize_device_id(device_id))

    def device_ids_for_user(self, user_id: str) -> set[str]:
        rows = device_mapper.device_ids_for_user(user_id)
        return {r if isinstance(r, str) else r["device_id"] for r in rows}

    def user_owns_device(self, user_id: str, device_id: str) -> bool:
        dev = self.get_device(device_id)
        if dev is None or dev.owner_user_id != user_id:
            return False
        return True

    def bind_device(self, user_id: str, device_id: str, *, display_name: str | None = None) -> Device:
        from deskbot_server.utils.device_data import ensure_device_data_initialized

        did = _normalize_device_id(device_id)
        if not _validate_device_id(did):
            raise ValueError("device_id 格式无效（允许字母数字 _ . -）")

        if not is_device_online(did):
            raise ValueError("绑定失败：设备未在线，请确认设备已开机并连接 Wi‑Fi")

        existing = device_mapper.get_by_device_id(did)
        if existing is not None:
            if existing.owner_user_id != user_id:
                raise ValueError("该设备已被其他账号绑定")
            name = (display_name or "").strip() or existing.display_name or did
            device_mapper.update_owner(existing.id, user_id, name)
            ensure_device_data_initialized(did)
            return device_mapper.get_by_device_id(did)

        name = (display_name or did).strip() or did
        device_mapper.insert(_new_id(), did, user_id, name)
        ensure_device_data_initialized(did)
        return device_mapper.get_by_device_id(did)

    def unbind_device(self, user_id: str, device_id: str) -> bool:
        affected = device_mapper.delete_by_device_id(_normalize_device_id(device_id), user_id)
        return affected > 0

    def reset_device_id(self, old_device_id: str) -> str:
        import random

        dev = device_mapper.get_by_device_id(old_device_id)
        if dev is None:
            raise ValueError("设备不存在")
        # 前缀 brfk_ + MAC(12位hex) + 随机4位
        prefix_end = old_device_id.find("_") + 1  # brfk_ 之后
        mac = old_device_id[prefix_end : prefix_end + 12]
        new_device_id = old_device_id[:prefix_end] + mac + str(random.randint(1000, 9999))
        # 重命名数据目录
        import shutil
        from pathlib import Path
        from deskbot_server.utils.paths import DATA_DIR

        old_dir = DATA_DIR / old_device_id
        new_dir = DATA_DIR / new_device_id
        if old_dir.is_dir():
            shutil.move(str(old_dir), str(new_dir))
        device_mapper.update_device_id(old_device_id, new_device_id)
        # 更新设备 display_name 中的旧 device_id
        if dev.display_name == old_device_id:
            device_mapper.update_owner(dev.id, dev.owner_user_id, new_device_id)
        return new_device_id
