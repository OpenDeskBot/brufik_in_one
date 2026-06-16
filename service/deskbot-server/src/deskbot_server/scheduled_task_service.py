"""Cron 定时任务 CRUD 与调度（东八区 / 北京时间）。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[misc, assignment]

from croniter import croniter
from sqlalchemy import select, update

from deskbot_server.db.engine import get_session
from deskbot_server.db.models import ScheduledTask, _new_id

_STATUS_ACTIVE = "active"
_STATUS_RUNNING = "running"
_STATUS_COMPLETED = "completed"
_STATUS_FAILED = "failed"

_TASK_ONCE = "once"
_TASK_RECURRING = "recurring"

_CRON_RE = re.compile(
    r"^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)$"
)

def _parse_cron_int(token: str) -> int | None:
    tok = str(token or "").strip()
    if not tok.isdigit():
        return None
    return int(tok)


def _repair_llm_cron_parts(parts: list[str]) -> list[str]:
    """纠正 LLM 常见误写（如 ``0 49 15 6 12`` → ``49 15 12 6 *``）。"""
    if len(parts) != 5:
        return parts
    mn, hr, dom, mon, dow = parts
    hr_i = _parse_cron_int(hr)
    mn_i = _parse_cron_int(mn)
    mon_i = _parse_cron_int(mon)
    dom_i = _parse_cron_int(dom)
    dow_i = _parse_cron_int(dow)

    # 0 49 15 6 12：前导 0 + 分/时/月/日顺序颠倒
    if (
        hr_i is not None
        and hr_i > 23
        and hr_i <= 59
        and mn_i is not None
        and mn_i <= 23
        and mon_i is not None
        and 1 <= mon_i <= 12
        and dow_i is not None
        and 1 <= dow_i <= 31
        and (dom_i is None or 0 <= dom_i <= 23)
    ):
        hour = dom_i if dom_i is not None else 0
        return [str(hr_i), str(hour), str(dow_i), str(mon_i), "*"]

    # 49 15 12 6 2026：末尾误加年份
    if (
        dow_i is not None
        and dow_i >= 1970
        and dom_i is not None
        and 1 <= dom_i <= 31
        and mon_i is not None
        and 1 <= mon_i <= 12
        and hr_i is not None
        and 0 <= hr_i <= 23
        and mn_i is not None
        and 0 <= mn_i <= 59
    ):
        return [mn, hr, dom, mon, "*"]

    return parts


if ZoneInfo is not None:
    CST = ZoneInfo("Asia/Shanghai")
else:
    CST = timezone(timedelta(hours=8))


def cst_now() -> datetime:
    return datetime.now(CST)


def format_cst(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CST)
    return dt.astimezone(CST).strftime("%Y-%m-%d %H:%M:%S")


def normalize_cron_expr(expr: str) -> str:
    raw = " ".join(str(expr or "").strip().split())
    parts = raw.split()
    if len(parts) == 6 and parts[0] in ("0", "00"):
        parts = parts[1:]
    if len(parts) == 6:
        raise ValueError(
            f"cron 表达式无效（须 5 段：分 时 日 月 周；勿含秒/年）: {expr!r}"
        )
    if len(parts) != 5:
        raise ValueError(f"cron 表达式无效（须 5 段：分 时 日 月 周）: {expr!r}")
    parts = _repair_llm_cron_parts(parts)
    raw = " ".join(parts)
    if not _CRON_RE.match(raw):
        raise ValueError(f"cron 表达式无效（须 5 段：分 时 日 月 周）: {expr!r}")
    mn_i = _parse_cron_int(parts[0])
    hr_i = _parse_cron_int(parts[1])
    if mn_i is not None and not (0 <= mn_i <= 59):
        raise ValueError(f"cron 分钟无效: {parts[0]!r}")
    if hr_i is not None and not (0 <= hr_i <= 23):
        raise ValueError(
            f"cron 小时无效: {parts[1]!r}（须 0–23；一次性任务格式为「分 时 日 月 *」，"
            f"例如 15:49 在 6 月 12 日应写 49 15 12 6 *）"
        )
    return raw


def validate_task_kind(kind: str) -> str:
    k = str(kind or "").strip().lower()
    if k in ("once", "onetime", "one_time", "one-time", "一次性", "单次"):
        return _TASK_ONCE
    if k in ("recurring", "repeat", "cron", "周期", "周期性", "循环"):
        return _TASK_RECURRING
    raise ValueError("task_kind 须为 once（一次性）或 recurring（周期性）")


def datetime_to_once_cron(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CST)
    local = dt.astimezone(CST)
    return f"{local.minute} {local.hour} {local.day} {local.month} *"


def delay_to_once_cron(*, delay_minutes: float | None = None, delay_seconds: float | None = None) -> str:
    base = cst_now()
    if delay_minutes is not None:
        base += timedelta(minutes=float(delay_minutes))
    elif delay_seconds is not None:
        base += timedelta(seconds=float(delay_seconds))
    else:
        raise ValueError("需要 delay_minutes 或 delay_seconds")
    return datetime_to_once_cron(base)


def _as_cst(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=CST)
    return dt.astimezone(CST)


def compute_next_run(cron_expr: str, *, base: datetime | None = None) -> datetime:
    expr = normalize_cron_expr(cron_expr)
    ref = _as_cst(base or cst_now())
    itr = croniter(expr, ref)
    nxt = itr.get_next(datetime)
    return _as_cst(nxt)


def _coerce_delay_minutes(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        v = float(raw)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _coerce_delay_seconds(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        v = float(raw)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def create_scheduled_task(
    device_id: str,
    description: str,
    *,
    cron: str | None = None,
    cron_expr: str | None = None,
    task_kind: str = _TASK_ONCE,
    delay_minutes: float | None = None,
    delay_seconds: float | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    dev = str(device_id or "").strip()
    desc = str(description or "").strip()
    if not dev:
        raise ValueError("device_id 不能为空")
    if not desc:
        raise ValueError("任务描述不能为空")

    kind = validate_task_kind(task_kind)
    expr_raw = str(cron or cron_expr or "").strip()
    if not expr_raw:
        if delay_minutes is not None or delay_seconds is not None:
            expr_raw = delay_to_once_cron(
                delay_minutes=delay_minutes,
                delay_seconds=delay_seconds,
            )
            kind = _TASK_ONCE
        else:
            raise ValueError("需要 cron 或 delay_minutes/delay_seconds")
    expr = normalize_cron_expr(expr_raw)
    nxt = compute_next_run(expr)
    if nxt <= cst_now():
        nxt = compute_next_run(expr, base=cst_now() + timedelta(seconds=1))

    session = get_session()
    sid = str(session_id or "").strip() or None
    row = ScheduledTask(
        id=_new_id(),
        device_id=dev,
        description=desc,
        cron_expr=expr,
        task_kind=kind,
        enabled=True,
        next_run_at=nxt,
        session_id=sid,
        status=_STATUS_ACTIVE,
    )
    session.add(row)
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(row)
    return _task_to_dict(row)


def get_scheduled_task(task_id: str, *, device_id: str | None = None) -> dict[str, Any] | None:
    tid = str(task_id or "").strip()
    if not tid:
        return None
    session = get_session()
    row = session.scalar(select(ScheduledTask).where(ScheduledTask.id == tid))
    if row is None:
        return None
    if device_id is not None and row.device_id != str(device_id).strip():
        return None
    return _task_to_dict(row)


def list_scheduled_tasks_for_device(device_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    dev = str(device_id or "").strip()
    if not dev:
        return []
    session = get_session()
    rows = session.scalars(
        select(ScheduledTask)
        .where(ScheduledTask.device_id == dev)
        .order_by(ScheduledTask.next_run_at.asc())
        .limit(max(1, min(int(limit), 500)))
    ).all()
    return [_task_to_dict(r) for r in rows]


def update_scheduled_task(
    task_id: str,
    *,
    device_id: str | None = None,
    description: str | None = None,
    cron: str | None = None,
    cron_expr: str | None = None,
    task_kind: str | None = None,
    enabled: bool | None = None,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    tid = str(task_id or "").strip()
    if not tid:
        raise ValueError("id 不能为空")
    session = get_session()
    row = session.scalar(select(ScheduledTask).where(ScheduledTask.id == tid))
    if row is None:
        return None
    if device_id is not None and row.device_id != str(device_id).strip():
        return None

    if description is not None:
        desc = str(description).strip()
        if not desc:
            raise ValueError("任务描述不能为空")
        row.description = desc
    expr_in = str(cron or cron_expr or "").strip()
    if expr_in:
        row.cron_expr = normalize_cron_expr(expr_in)
        row.next_run_at = compute_next_run(row.cron_expr)
    if task_kind is not None:
        row.task_kind = validate_task_kind(task_kind)
    if enabled is not None:
        row.enabled = bool(enabled)
        if row.enabled and row.status in (_STATUS_COMPLETED, _STATUS_FAILED):
            row.status = _STATUS_ACTIVE
    if session_id is not None:
        row.session_id = str(session_id).strip() or None
    if row.enabled and row.status == _STATUS_ACTIVE:
        if _as_cst(row.next_run_at) <= cst_now():
            row.next_run_at = compute_next_run(row.cron_expr, base=cst_now() + timedelta(seconds=1))
    session.commit()
    session.refresh(row)
    return _task_to_dict(row)


def delete_scheduled_task(task_id: str, *, device_id: str | None = None) -> bool:
    tid = str(task_id or "").strip()
    if not tid:
        return False
    session = get_session()
    row = session.scalar(select(ScheduledTask).where(ScheduledTask.id == tid))
    if row is None:
        return False
    if device_id is not None and row.device_id != str(device_id).strip():
        return False
    session.delete(row)
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    return True


def execute_schedule_task_tool(
    raw: dict[str, Any],
    *,
    device_id: str,
    default_session_id: str | None = None,
) -> dict[str, Any]:
    """LLM ``schedule_task`` 工具：增删改查。"""
    dev = str(device_id or "").strip()
    if not dev:
        raise ValueError("schedule_task 需要 device_id")
    action = str(raw.get("action") or raw.get("op") or "create").strip().lower()
    if action in ("create", "add", "new"):
        sid = str(raw.get("session_id") or default_session_id or "").strip() or None
        entry = create_scheduled_task(
            dev,
            str(raw.get("task") or raw.get("description") or raw.get("text") or "").strip(),
            cron=str(raw.get("cron") or raw.get("cron_expr") or "").strip() or None,
            task_kind=str(raw.get("task_kind") or raw.get("kind") or _TASK_ONCE),
            delay_minutes=_coerce_delay_minutes(
                raw.get("delay_minutes")
                or raw.get("minutes")
                or raw.get("delay_minute")
                or raw.get("minute")
            ),
            delay_seconds=_coerce_delay_seconds(
                raw.get("delay_seconds") or raw.get("seconds") or raw.get("second")
            ),
            session_id=sid,
        )
        return {"tool": "schedule_task", "action": "create", "ok": True, **entry}
    if action in ("list", "ls", "query_all"):
        tasks = list_scheduled_tasks_for_device(dev)
        return {"tool": "schedule_task", "action": "list", "ok": True, "tasks": tasks, "count": len(tasks)}
    tid = str(raw.get("id") or raw.get("task_id") or "").strip()
    if action in ("get", "read", "query"):
        if not tid:
            raise ValueError("get 需要 id")
        row = get_scheduled_task(tid, device_id=dev)
        if row is None:
            raise ValueError(f"未找到任务 id={tid}")
        return {"tool": "schedule_task", "action": "get", "ok": True, "task": row}
    if action in ("update", "edit", "modify"):
        if not tid:
            raise ValueError("update 需要 id")
        row = update_scheduled_task(
            tid,
            device_id=dev,
            description=raw.get("task") or raw.get("description") or raw.get("text"),
            cron=str(raw.get("cron") or raw.get("cron_expr") or "").strip() or None,
            task_kind=raw.get("task_kind") or raw.get("kind"),
            enabled=raw.get("enabled") if "enabled" in raw else None,
            session_id=str(raw.get("session_id") or "").strip() or None
            if "session_id" in raw
            else None,
        )
        if row is None:
            raise ValueError(f"未找到任务 id={tid}")
        return {"tool": "schedule_task", "action": "update", "ok": True, **row}
    if action in ("delete", "remove", "del"):
        if not tid:
            raise ValueError("delete 需要 id")
        if not delete_scheduled_task(tid, device_id=dev):
            raise ValueError(f"未找到任务 id={tid}")
        return {"tool": "schedule_task", "action": "delete", "ok": True, "id": tid}
    raise ValueError(f"未知 action: {action!r}")


def expire_overdue_active_tasks(*, lookback_minutes: float = 5.0) -> int:
    """一次性任务超过回溯窗口未执行则标为失败；周期性任务跳到下一触发点。"""
    now = cst_now()
    cutoff = now - timedelta(minutes=max(1.0, float(lookback_minutes)))
    session = get_session()
    rows = session.scalars(
        select(ScheduledTask).where(
            ScheduledTask.enabled.is_(True),
            ScheduledTask.status == _STATUS_ACTIVE,
            ScheduledTask.next_run_at < cutoff,
        )
    ).all()
    n = 0
    for row in rows:
        if row.task_kind == _TASK_RECURRING:
            row.next_run_at = compute_next_run(row.cron_expr, base=now)
        else:
            row.status = _STATUS_FAILED
            row.enabled = False
            row.executed_at = now
            row.result_summary = "超过执行窗口未执行"
        n += 1
    if n:
        session.commit()
    return n


def claim_due_tasks(
    *,
    limit: int = 10,
    lookback_minutes: float = 5.0,
) -> list[dict[str, Any]]:
    now = cst_now()
    cutoff = now - timedelta(minutes=max(1.0, float(lookback_minutes)))
    session = get_session()
    rows = session.scalars(
        select(ScheduledTask)
        .where(
            ScheduledTask.enabled.is_(True),
            ScheduledTask.status == _STATUS_ACTIVE,
            ScheduledTask.next_run_at <= now,
            ScheduledTask.next_run_at >= cutoff,
        )
        .order_by(ScheduledTask.next_run_at.asc())
        .limit(max(1, min(int(limit), 50)))
    ).all()
    claimed: list[dict[str, Any]] = []
    for row in rows:
        result = session.execute(
            update(ScheduledTask)
            .where(
                ScheduledTask.id == row.id,
                ScheduledTask.status == _STATUS_ACTIVE,
                ScheduledTask.enabled.is_(True),
            )
            .values(status=_STATUS_RUNNING)
        )
        if result.rowcount:
            session.commit()
            session.refresh(row)
            claimed.append(_task_to_dict(row))
        else:
            session.rollback()
    return claimed


def finish_scheduled_task(
    task_id: str,
    *,
    ok: bool,
    summary: str | None = None,
) -> None:
    tid = str(task_id or "").strip()
    if not tid:
        return
    session = get_session()
    row = session.scalar(select(ScheduledTask).where(ScheduledTask.id == tid))
    if row is None:
        return
    now = cst_now()
    row.executed_at = now
    row.result_summary = (summary or "")[:4000] or None
    if row.task_kind == _TASK_RECURRING and row.enabled:
        row.status = _STATUS_ACTIVE
        row.next_run_at = compute_next_run(row.cron_expr, base=now + timedelta(seconds=1))
    else:
        row.status = _STATUS_COMPLETED if ok else _STATUS_FAILED
        row.enabled = False
    session.commit()


def _task_to_dict(row: ScheduledTask) -> dict[str, Any]:
    return {
        "id": row.id,
        "device_id": row.device_id,
        "description": row.description,
        "cron": row.cron_expr,
        "cron_expr": row.cron_expr,
        "task_kind": row.task_kind,
        "enabled": bool(row.enabled),
        "next_run_at": format_cst(row.next_run_at),
        "session_id": row.session_id,
        "status": row.status,
        "result_summary": row.result_summary,
        "created_at": format_cst(row.created_at),
        "executed_at": format_cst(row.executed_at),
    }
