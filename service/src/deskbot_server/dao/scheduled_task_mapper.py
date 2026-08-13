"""定时任务表 SQL Mapper — MyBatis 注解风格。

从 scheduled_task_service.py 提取的纯 SQL 声明，方法体为空。
"""

from __future__ import annotations

from datetime import datetime

from deskbot_server.db.models import ScheduledTask
from deskbot_server.db.sql_decorators import execute, select, select_one

# ────────────────────────── 查询 ──────────────────────────


@select_one("SELECT * FROM scheduled_tasks WHERE id = :task_id", model=ScheduledTask)
def get_by_id(task_id: str) -> ScheduledTask | None:
    """根据 ID 查找任务。"""


@select(
    """
    SELECT * FROM scheduled_tasks
    WHERE device_id = :device_id
    ORDER BY next_run_at ASC
    LIMIT :limit OFFSET :offset
    """,
    model=ScheduledTask,
)
def list_for_device(device_id: str, limit: int = 100, offset: int = 0) -> list[ScheduledTask]:
    """列出设备的定时任务。"""


@select_one("SELECT COUNT(*) FROM scheduled_tasks WHERE device_id = :device_id")
def count_for_device(device_id: str) -> int:
    """统计设备任务数。"""


@select(
    """
    SELECT * FROM scheduled_tasks
    WHERE enabled = 1 AND status = :status AND next_run_at < :cutoff
    """,
    model=ScheduledTask,
)
def list_overdue_active(status: str, cutoff: datetime) -> list[ScheduledTask]:
    """查找超期未执行的活跃任务。"""


@select(
    """
    SELECT * FROM scheduled_tasks
    WHERE enabled = 1 AND status = :status AND next_run_at <= :now AND next_run_at >= :cutoff
    ORDER BY next_run_at ASC
    LIMIT :limit
    """,
    model=ScheduledTask,
)
def list_due_tasks(status: str, now: datetime, cutoff: datetime, limit: int = 10) -> list[ScheduledTask]:
    """查找到期可执行的任务。"""


# ────────────────────────── 写操作 ──────────────────────────


@execute(
    """
    INSERT INTO scheduled_tasks (id, device_id, description, cron_expr, task_kind, enabled, next_run_at, session_id, status)
    VALUES (:uid, :device_id, :description, :cron_expr, :task_kind, 1, :next_run_at, :session_id, :status)
    """
)
def insert(
    uid: str,
    device_id: str,
    description: str,
    cron_expr: str,
    task_kind: str,
    next_run_at: datetime,
    session_id: str | None,
    status: str,
) -> int:
    """插入新任务。"""


@execute(
    """
    UPDATE scheduled_tasks
    SET status = :status
    WHERE id = :task_id AND status = :expected_status AND enabled = 1
    """
)
def claim_task(task_id: str, status: str, expected_status: str) -> int:
    """CAS 抢占任务（乐观锁）。返回 0 表示抢占失败。"""


@execute(
    """
    UPDATE scheduled_tasks
    SET description = :description,
        cron_expr   = :cron_expr,
        task_kind   = :task_kind,
        enabled     = :enabled,
        next_run_at = :next_run_at,
        session_id  = :session_id,
        status      = :status
    WHERE id = :task_id
    """
)
def update_fields(
    task_id: str,
    description: str,
    cron_expr: str,
    task_kind: str,
    enabled: bool,
    next_run_at: datetime,
    session_id: str | None,
    status: str,
) -> int:
    """更新任务字段。"""


@execute(
    """
    UPDATE scheduled_tasks
    SET status        = :status,
        executed_at   = :executed_at,
        result_summary = :result_summary,
        enabled       = :enabled
    WHERE id = :task_id
    """
)
def finish(task_id: str, status: str, executed_at: datetime, result_summary: str | None, enabled: bool) -> int:
    """完成任务（一次性）。"""


@execute(
    """
    UPDATE scheduled_tasks
    SET executed_at    = :executed_at,
        result_summary = :result_summary,
        next_run_at    = :next_run_at,
        status         = :status
    WHERE id = :task_id
    """
)
def finish_recurring(
    task_id: str, executed_at: datetime, result_summary: str | None, next_run_at: datetime, status: str
) -> int:
    """完成周期任务（跳到下一触发点）。"""


@execute("DELETE FROM scheduled_tasks WHERE id = :task_id")
def delete(task_id: str) -> int:
    """删除任务。"""


@execute(
    "UPDATE scheduled_tasks SET status = :status, enabled = 0, executed_at = :executed_at, result_summary = :result_summary WHERE id = :task_id"
)
def expire(task_id: str, status: str, executed_at: datetime, result_summary: str) -> int:
    """标记一次性任务为过期失败。"""


@execute("UPDATE scheduled_tasks SET next_run_at = :next_run_at WHERE id = :task_id")
def reschedule(task_id: str, next_run_at: datetime) -> int:
    """更新周期任务下次执行时间。"""
