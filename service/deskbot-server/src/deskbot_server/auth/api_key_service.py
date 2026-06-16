from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from deskbot_server.db.engine import get_session
from deskbot_server.db.models import ApiKey, UsageDaily, UsageDailyDevice

FREE_DAILY_QUOTA_BYTES = 1_073_741_824  # 1 GiB
FREE_FILE_KEY_ID = "00000000-0000-4000-8000-000000000001"
FREE_FILE_KEY_HASH_SENTINEL = "__file_based_free_key__"
USAGE_CATEGORIES = ("asr", "face", "llm", "tts")


class QuotaExceededError(Exception):
    pass


@dataclass(frozen=True)
class ApiKeyAuth:
    api_key_id: str
    user_id: str | None
    is_free: bool
    daily_quota_bytes: int
    name: str


@dataclass(frozen=True)
class FreeApiKeyConfig:
    name: str
    api_key: str
    daily_quota_bytes: int


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> date:
    return _utcnow().date()


def generate_raw_key(*, free: bool = False) -> str:
    token = secrets.token_urlsafe(24)
    return f"odk_free_{token}" if free else f"odk_{token}"


def create_api_key(
    user_id: str,
    *,
    name: str,
    daily_quota_bytes: int = 0,
) -> tuple[str, ApiKey]:
    raw = generate_raw_key(free=False)
    row = ApiKey(
        user_id=user_id,
        name=(name or "default").strip()[:128],
        key_hash=_hash_key(raw),
        key_prefix=raw[:12],
        is_free=False,
        daily_quota_bytes=max(0, int(daily_quota_bytes)),
        is_active=True,
    )
    session = get_session()
    session.add(row)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ValueError("创建 API Key 失败") from exc
    session.refresh(row)
    session.expunge(row)
    return raw, row


def _free_api_key_path() -> "Path":
    from deskbot_server.db.engine import default_db_path

    return default_db_path().parent / ".free_api_key"


def read_free_api_key_config() -> FreeApiKeyConfig | None:
    """读取 ``data/.free_api_key``（免费 Key 唯一来源，不查数据库）。"""
    path = _free_api_key_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    name = "免费体验 Key"
    api_key = ""
    daily_quota_bytes = FREE_DAILY_QUOTA_BYTES
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if key == "name":
            name = val or name
        elif key == "api_key":
            api_key = val
        elif key == "daily_quota_bytes":
            try:
                daily_quota_bytes = max(0, int(val))
            except ValueError:
                pass
    if not api_key:
        return None
    return FreeApiKeyConfig(name=name, api_key=api_key, daily_quota_bytes=daily_quota_bytes)


def read_free_api_key_raw() -> str | None:
    """读取 ``data/.free_api_key`` 中的体验 Key（供已登录调试台订阅 WS）。"""
    cfg = read_free_api_key_config()
    return cfg.api_key if cfg else None


def write_free_api_key_file(raw_key: str, *, daily_quota_bytes: int = FREE_DAILY_QUOTA_BYTES) -> None:
    """写入 ``data/.free_api_key``（首次启动或手动重置时使用）。"""
    path = _free_api_key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"name=免费体验 Key\napi_key={raw_key}\n"
        f"daily_quota_bytes={daily_quota_bytes}\n",
        encoding="utf-8",
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass


def ensure_free_usage_placeholder() -> None:
    """确保用量统计占位行存在（Key 本身仅存于文件，不入库）。"""
    session = get_session()
    row = session.get(ApiKey, FREE_FILE_KEY_ID)
    if row is not None:
        return
    row = ApiKey(
        id=FREE_FILE_KEY_ID,
        user_id=None,
        name="免费体验 Key",
        key_hash=FREE_FILE_KEY_HASH_SENTINEL,
        key_prefix="odk_free_",
        is_free=True,
        daily_quota_bytes=FREE_DAILY_QUOTA_BYTES,
        is_active=True,
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()


def list_api_keys_for_user(user_id: str) -> list[ApiKey]:
    session = get_session()
    rows = session.scalars(
        select(ApiKey)
        .where(ApiKey.user_id == user_id, ApiKey.is_active.is_(True))
        .order_by(ApiKey.created_at.desc())
    ).all()
    for row in rows:
        session.expunge(row)
    return list(rows)


def revoke_api_key(user_id: str, key_id: str) -> bool:
    session = get_session()
    row = session.scalar(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user_id, ApiKey.is_free.is_(False))
    )
    if row is None:
        return False
    row.is_active = False
    session.commit()
    return True


def _authenticate_free_file_key(token: str) -> ApiKeyAuth | None:
    cfg = read_free_api_key_config()
    if cfg is None or token != cfg.api_key:
        return None
    ensure_free_usage_placeholder()
    session = get_session()
    row = session.get(ApiKey, FREE_FILE_KEY_ID)
    if row is not None:
        row.last_used_at = _utcnow()
        session.commit()
    return ApiKeyAuth(
        api_key_id=FREE_FILE_KEY_ID,
        user_id=None,
        is_free=True,
        daily_quota_bytes=cfg.daily_quota_bytes,
        name=cfg.name,
    )


def authenticate_api_key(raw_key: str) -> ApiKeyAuth | None:
    token = (raw_key or "").strip()
    if not token:
        return None
    free_auth = _authenticate_free_file_key(token)
    if free_auth is not None:
        return free_auth
    if token.startswith("odk_free_"):
        return None
    session = get_session()
    row = session.scalar(select(ApiKey).where(ApiKey.key_hash == _hash_key(token), ApiKey.is_active.is_(True)))
    if row is None:
        return None
    row.last_used_at = _utcnow()
    session.commit()
    return ApiKeyAuth(
        api_key_id=row.id,
        user_id=row.user_id,
        is_free=bool(row.is_free),
        daily_quota_bytes=int(row.daily_quota_bytes or 0),
        name=row.name,
    )


def _normalize_device_id(device_id: str | None) -> str | None:
    val = (device_id or "").strip()
    return val or None


def _usage_row(session, api_key_id: str, usage_date: date) -> UsageDaily:
    row = session.scalar(
        select(UsageDaily).where(
            UsageDaily.api_key_id == api_key_id,
            UsageDaily.usage_date == usage_date,
        )
    )
    if row is not None:
        return row
    row = UsageDaily(api_key_id=api_key_id, usage_date=usage_date)
    session.add(row)
    session.flush()
    return row


def _usage_device_row(session, api_key_id: str, device_id: str, usage_date: date) -> UsageDailyDevice:
    row = session.scalar(
        select(UsageDailyDevice).where(
            UsageDailyDevice.api_key_id == api_key_id,
            UsageDailyDevice.device_id == device_id,
            UsageDailyDevice.usage_date == usage_date,
        )
    )
    if row is not None:
        return row
    row = UsageDailyDevice(api_key_id=api_key_id, device_id=device_id, usage_date=usage_date)
    session.add(row)
    session.flush()
    return row


def _apply_usage_delta(row, category: str, byte_count: int) -> None:
    if category == "asr":
        row.asr_bytes = int(row.asr_bytes) + byte_count
    elif category == "face":
        row.face_bytes = int(row.face_bytes) + byte_count
    elif category == "llm":
        row.llm_bytes = int(row.llm_bytes) + byte_count
    elif category == "tts":
        row.tts_bytes = int(row.tts_bytes) + byte_count


def record_usage(
    api_key_id: str,
    category: str,
    byte_count: int,
    *,
    device_id: str | None = None,
) -> None:
    if byte_count <= 0:
        return
    if category not in USAGE_CATEGORIES:
        return
    session = get_session()
    row = session.get(ApiKey, api_key_id)
    if row is None or not row.is_active:
        return
    today = _today()
    usage = _usage_row(session, api_key_id, today)
    _apply_usage_delta(usage, category, byte_count)
    dev_id = _normalize_device_id(device_id)
    if dev_id:
        dev_usage = _usage_device_row(session, api_key_id, dev_id, today)
        _apply_usage_delta(dev_usage, category, byte_count)
    session.commit()


def _free_key_quota_bytes() -> int:
    cfg = read_free_api_key_config()
    return cfg.daily_quota_bytes if cfg else FREE_DAILY_QUOTA_BYTES


def check_quota(api_key_id: str, additional_bytes: int = 0) -> None:
    if api_key_id == FREE_FILE_KEY_ID:
        quota = _free_key_quota_bytes()
        if quota <= 0:
            return
        session = get_session()
        usage = session.scalar(
            select(UsageDaily).where(UsageDaily.api_key_id == api_key_id, UsageDaily.usage_date == _today())
        )
        used = usage.total_bytes if usage else 0
        if used + max(0, additional_bytes) > quota:
            raise QuotaExceededError("quota_exhausted")
        return
    session = get_session()
    row = session.get(ApiKey, api_key_id)
    if row is None or not row.is_active:
        raise PermissionError("invalid_api_key")
    quota = int(row.daily_quota_bytes or 0)
    if quota <= 0:
        return
    usage = session.scalar(
        select(UsageDaily).where(UsageDaily.api_key_id == api_key_id, UsageDaily.usage_date == _today())
    )
    used = usage.total_bytes if usage else 0
    if used + max(0, additional_bytes) > quota:
        raise QuotaExceededError("quota_exhausted")


def record_usage_checked(
    api_key_id: str,
    category: str,
    byte_count: int,
    *,
    device_id: str | None = None,
) -> None:
    if byte_count <= 0:
        return
    check_quota(api_key_id, byte_count)
    record_usage(api_key_id, category, byte_count, device_id=device_id)


def get_api_key_usage_today(api_key_id: str) -> dict:
    session = get_session()
    usage = session.scalar(
        select(UsageDaily).where(UsageDaily.api_key_id == api_key_id, UsageDaily.usage_date == _today())
    )
    if api_key_id == FREE_FILE_KEY_ID:
        quota = _free_key_quota_bytes()
    else:
        row = session.get(ApiKey, api_key_id)
        quota = int(row.daily_quota_bytes or 0) if row else 0
    if usage is None:
        return {
            "asr_bytes": 0,
            "face_bytes": 0,
            "llm_bytes": 0,
            "tts_bytes": 0,
            "total_bytes": 0,
            "quota_bytes": quota,
        }
    return {
        "asr_bytes": int(usage.asr_bytes),
        "face_bytes": int(usage.face_bytes),
        "llm_bytes": int(usage.llm_bytes),
        "tts_bytes": int(usage.tts_bytes),
        "total_bytes": usage.total_bytes,
        "quota_bytes": quota,
    }


def _device_ids_for_user(user_id: str) -> list[str]:
    from deskbot_server.auth.device_service import list_devices_for_user

    return [d.device_id for d in list_devices_for_user(user_id)]


def _aggregate_usage_rows(rows) -> dict:
    totals = _empty_totals()
    for usage in rows:
        totals["asr_bytes"] += int(usage.asr_bytes)
        totals["face_bytes"] += int(usage.face_bytes)
        totals["llm_bytes"] += int(usage.llm_bytes)
        totals["tts_bytes"] += int(usage.tts_bytes)
        totals["total_bytes"] += usage.total_bytes
    return totals


def get_user_usage_today(user_id: str) -> dict:
    device_ids = _device_ids_for_user(user_id)
    if not device_ids:
        return _empty_totals()

    session = get_session()
    rows = session.scalars(
        select(UsageDailyDevice).where(
            UsageDailyDevice.device_id.in_(device_ids),
            UsageDailyDevice.usage_date == _today(),
        )
    ).all()
    return _aggregate_usage_rows(rows)


def get_user_usage_summary(user_id: str, *, days: int = 7) -> dict:
    from datetime import timedelta

    session = get_session()
    keys = session.scalars(select(ApiKey).where(ApiKey.user_id == user_id, ApiKey.is_active.is_(True))).all()
    key_ids = [k.id for k in keys]
    device_ids = _device_ids_for_user(user_id)

    start = _today() - timedelta(days=max(1, days) - 1)

    per_key: dict[str, dict] = {}
    if key_ids:
        key_rows = session.scalars(
            select(UsageDaily)
            .where(UsageDaily.api_key_id.in_(key_ids), UsageDaily.usage_date >= start)
            .order_by(UsageDaily.usage_date.desc())
        ).all()
        for usage in key_rows:
            kid = usage.api_key_id
            bucket = per_key.setdefault(
                kid,
                {
                    "api_key_id": kid,
                    "days": [],
                    "asr_bytes": 0,
                    "face_bytes": 0,
                    "llm_bytes": 0,
                    "tts_bytes": 0,
                    "total_bytes": 0,
                },
            )
            day_row = {
                "date": usage.usage_date.isoformat(),
                "asr_bytes": int(usage.asr_bytes),
                "face_bytes": int(usage.face_bytes),
                "llm_bytes": int(usage.llm_bytes),
                "tts_bytes": int(usage.tts_bytes),
                "total_bytes": usage.total_bytes,
            }
            bucket["days"].append(day_row)
            for field in ("asr_bytes", "face_bytes", "llm_bytes", "tts_bytes", "total_bytes"):
                bucket[field] += day_row[field]

    totals = _empty_totals()
    if device_ids:
        device_rows = session.scalars(
            select(UsageDailyDevice).where(
                UsageDailyDevice.device_id.in_(device_ids),
                UsageDailyDevice.usage_date >= start,
            )
        ).all()
        totals = _aggregate_usage_rows(device_rows)

    key_meta = {k.id: k for k in keys}
    result_keys = []
    for kid, stats in per_key.items():
        meta = key_meta.get(kid)
        result_keys.append(
            {
                "api_key_id": kid,
                "name": meta.name if meta else kid,
                "key_prefix": meta.key_prefix if meta else "",
                **stats,
            }
        )
    return {"key_stats": result_keys, "totals": totals}


def get_user_device_usage_summary(user_id: str, *, days: int = 14) -> dict:
    from deskbot_server.auth.device_service import list_devices_for_user

    devices = list_devices_for_user(user_id)
    device_ids = [d.device_id for d in devices]
    if not device_ids:
        return {"device_stats": [], "totals": _empty_totals(), "today_by_device": []}

    from datetime import timedelta

    session = get_session()
    start = _today() - timedelta(days=max(1, days) - 1)
    rows = session.scalars(
        select(UsageDailyDevice)
        .where(
            UsageDailyDevice.device_id.in_(device_ids),
            UsageDailyDevice.usage_date >= start,
        )
        .order_by(UsageDailyDevice.usage_date.desc())
    ).all()

    per_device: dict[str, dict] = {}
    totals = _empty_totals()
    for usage in rows:
        did = usage.device_id
        bucket = per_device.setdefault(
            did,
            {
                "device_id": did,
                "day_map": {},
                "asr_bytes": 0,
                "face_bytes": 0,
                "llm_bytes": 0,
                "tts_bytes": 0,
                "total_bytes": 0,
            },
        )
        date_key = usage.usage_date.isoformat()
        day_row = bucket["day_map"].setdefault(
            date_key,
            {
                "date": date_key,
                "asr_bytes": 0,
                "face_bytes": 0,
                "llm_bytes": 0,
                "tts_bytes": 0,
                "total_bytes": 0,
            },
        )
        for field in ("asr_bytes", "face_bytes", "llm_bytes", "tts_bytes"):
            val = int(getattr(usage, field))
            day_row[field] += val
            bucket[field] += val
            totals[field] += val
        day_row["total_bytes"] += usage.total_bytes
        bucket["total_bytes"] += usage.total_bytes
        totals["total_bytes"] += usage.total_bytes

    device_meta = {d.device_id: d for d in devices}
    device_stats = []
    for did, stats in per_device.items():
        meta = device_meta.get(did)
        days = sorted(stats.pop("day_map", {}).values(), key=lambda x: x["date"], reverse=True)
        device_stats.append(
            {
                "device_id": did,
                "display_name": (meta.display_name if meta else None) or did,
                "days": days,
                **{k: v for k, v in stats.items() if k != "device_id"},
            }
        )
    device_stats.sort(key=lambda x: x.get("total_bytes", 0), reverse=True)

    today_rows = session.scalars(
        select(UsageDailyDevice)
        .where(
            UsageDailyDevice.device_id.in_(device_ids),
            UsageDailyDevice.usage_date == _today(),
        )
    ).all()
    today_map: dict[str, dict] = {}
    for usage in today_rows:
        did = usage.device_id
        bucket = today_map.setdefault(
            did,
            {"device_id": did, "asr_bytes": 0, "face_bytes": 0, "llm_bytes": 0, "tts_bytes": 0, "total_bytes": 0},
        )
        for field in ("asr_bytes", "face_bytes", "llm_bytes", "tts_bytes", "total_bytes"):
            bucket[field] += int(getattr(usage, field) if field != "total_bytes" else usage.total_bytes)

    today_by_device = []
    for did in device_ids:
        meta = device_meta.get(did)
        row = today_map.get(
            did,
            {"device_id": did, "asr_bytes": 0, "face_bytes": 0, "llm_bytes": 0, "tts_bytes": 0, "total_bytes": 0},
        )
        today_by_device.append(
            {
                **row,
                "display_name": (meta.display_name if meta else None) or did,
            }
        )

    return {"device_stats": device_stats, "totals": totals, "today_by_device": today_by_device}


def _empty_totals() -> dict:
    return {"asr_bytes": 0, "face_bytes": 0, "llm_bytes": 0, "tts_bytes": 0, "total_bytes": 0}


def extract_api_key_from_query(qargs: dict) -> str | None:
    for key in ("api_key", "apikey", "key"):
        val = (qargs.get(key) or "").strip()
        if val:
            return val
    return None


def extract_api_key_from_headers(headers) -> str | None:
    if headers is None:
        return None
    raw = (headers.get("X-API-Key") or headers.get("x-api-key") or "").strip()
    if raw:
        return raw
    auth = (headers.get("Authorization") or headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None
