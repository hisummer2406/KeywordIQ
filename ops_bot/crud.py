"""ops schema 的数据访问层。纯数据库操作，不含业务判断（同构项目 `*_crud.py` 分层约定）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ops_bot.model import (
    TASK_CONFIRMED,
    TASK_EXPIRED,
    TASK_FAILED,
    TASK_PENDING_CONFIRM,
    TASK_RUNNING,
    TASK_SUCCEEDED,
    OpsAlertState,
    OpsBackupRecord,
    OpsTask,
)


async def create_task(
    session: AsyncSession, *, kind: str, payload: dict | None = None, requested_by_open_id: str | None = None
) -> str:
    """新建一条 RUNNING 任务记录，返回 task_id。"""
    task_id = str(uuid.uuid4())
    session.add(
        OpsTask(
            id=task_id,
            kind=kind,
            status=TASK_RUNNING,
            payload=payload or {},
            requested_by_open_id=requested_by_open_id,
        )
    )
    await session.flush()
    return task_id


async def mark_task_succeeded(session: AsyncSession, task_id: str, output: str = "") -> None:
    await session.execute(
        update(OpsTask)
        .where(OpsTask.id == task_id)
        .values(status=TASK_SUCCEEDED, output=output, finished_at=func.now())
    )


async def mark_task_failed(session: AsyncSession, task_id: str, output: str = "") -> None:
    await session.execute(
        update(OpsTask)
        .where(OpsTask.id == task_id)
        .values(status=TASK_FAILED, output=output, finished_at=func.now())
    )


async def create_pending_confirm_task(
    session: AsyncSession,
    *,
    kind: str,
    payload: dict,
    requested_by_open_id: str | None,
    ttl_minutes: int,
) -> tuple[str, str]:
    """新建一条待确认任务，返回 `(task_id, confirm_token)`——重启/发布专用
    （design.md D5）。`confirm_token` 与 `task_id` 一起编进卡片按钮的 value，
    回调时两者都要匹配，防止已失效卡片的按钮被重放。
    """
    task_id = str(uuid.uuid4())
    confirm_token = str(uuid.uuid4())
    session.add(
        OpsTask(
            id=task_id,
            kind=kind,
            status=TASK_PENDING_CONFIRM,
            payload=payload,
            confirm_token=confirm_token,
            confirm_expires_at=datetime.now(UTC) + timedelta(minutes=ttl_minutes),
            requested_by_open_id=requested_by_open_id,
        )
    )
    await session.flush()
    return task_id, confirm_token


async def get_task(session: AsyncSession, task_id: str) -> OpsTask | None:
    return await session.get(OpsTask, task_id)


async def try_confirm_task(session: AsyncSession, task_id: str) -> bool:
    """乐观锁：只有仍处于 `PENDING_CONFIRM` 时才能转 `CONFIRMED`。返回 `True`
    表示这次调用真正完成了转移——并发重复点击（双击、飞书重放）只有一次会拿到
    `True`，其余直接判"已处理"，不会执行两次（design.md D5）。
    """
    result = await session.execute(
        update(OpsTask)
        .where(OpsTask.id == task_id, OpsTask.status == TASK_PENDING_CONFIRM)
        .values(status=TASK_CONFIRMED)
    )
    return result.rowcount > 0


async def mark_task_expired(session: AsyncSession, task_id: str) -> None:
    await session.execute(
        update(OpsTask)
        .where(OpsTask.id == task_id, OpsTask.status == TASK_PENDING_CONFIRM)
        .values(status=TASK_EXPIRED, finished_at=func.now())
    )


async def mark_task_running(session: AsyncSession, task_id: str) -> None:
    await session.execute(update(OpsTask).where(OpsTask.id == task_id).values(status=TASK_RUNNING))


async def last_succeeded_at(session: AsyncSession, kind: str) -> datetime | None:
    """某类任务最近一次成功完成的时间；从未成功过则 `None`。

    调度循环靠它判断"距上次成功执行的时间"，而不是进程内存里的下次触发时间戳
    ——见 `ops_bot/scheduler.py` 与 design.md D1。
    """
    result = await session.execute(
        select(OpsTask.finished_at)
        .where(OpsTask.kind == kind, OpsTask.status == TASK_SUCCEEDED)
        .order_by(OpsTask.finished_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_unresolved_alerts(session: AsyncSession) -> list[OpsAlertState]:
    result = await session.execute(
        select(OpsAlertState)
        .where(OpsAlertState.resolved_at.is_(None), OpsAlertState.last_fired_at.is_not(None))
        .order_by(OpsAlertState.last_fired_at.desc())
    )
    return list(result.scalars())


async def get_alert_state(session: AsyncSession, alert_key: str) -> OpsAlertState | None:
    result = await session.execute(select(OpsAlertState).where(OpsAlertState.alert_key == alert_key))
    return result.scalar_one_or_none()


async def upsert_alert_fired(session: AsyncSession, alert_key: str, level: str, consecutive_count: int) -> None:
    """记录一次"仍处于异常"的判定：`last_fired_at`/`level`/`consecutive_count` 写成
    当前值，`resolved_at` 清空。`ops_bot/alerts.py` 决定是否要因此真的发一条通知——
    这层只负责状态落库，不含"要不要发"的判断（分层约定：crud 不含业务判断）。
    """
    await session.execute(
        text(
            "INSERT INTO ops.ops_alert_state (alert_key, level, last_fired_at, consecutive_count, resolved_at) "
            "VALUES (:key, :level, now(), :count, NULL) "
            "ON CONFLICT (alert_key) DO UPDATE SET "
            "level = :level, last_fired_at = now(), consecutive_count = :count, resolved_at = NULL"
        ),
        {"key": alert_key, "level": level, "count": consecutive_count},
    )


async def bump_alert_consecutive_count(session: AsyncSession, alert_key: str, consecutive_count: int) -> None:
    """只推进"连续异常计数"，不动 `last_fired_at`——静默期内仍在异常的轮次要计数，
    但不发新通知，`last_fired_at` 必须保持"上一次真正发送通知的时刻"这个含义不变，
    否则静默期判定会被每一轮都重置、永远不会到期（`ops_bot/alerts.py`）。
    """
    await session.execute(
        text("UPDATE ops.ops_alert_state SET consecutive_count = :count WHERE alert_key = :key"),
        {"key": alert_key, "count": consecutive_count},
    )


async def mark_alert_resolved(session: AsyncSession, alert_key: str) -> None:
    await session.execute(
        text(
            "UPDATE ops.ops_alert_state SET resolved_at = now(), consecutive_count = 0 "
            "WHERE alert_key = :key"
        ),
        {"key": alert_key},
    )


async def set_alert_maintenance_until(session: AsyncSession, alert_key: str, until: datetime) -> None:
    """打开/延长一个告警键的维护窗口（design.md D3「维护窗口」）。

    用 `ON CONFLICT` 而不要求该键必须先存在：重启/发布可能在这个告警键从未触发过
    （从未异常过）时就开始执行，此时也要能提前立好维护窗口，等巡检真的检测到异常时
    直接读到它，不必区分"这个键有没有历史记录"。
    """
    await session.execute(
        text(
            "INSERT INTO ops.ops_alert_state (alert_key, maintenance_until) VALUES (:key, :until) "
            "ON CONFLICT (alert_key) DO UPDATE SET maintenance_until = :until"
        ),
        {"key": alert_key, "until": until},
    )


async def try_claim_dedup_key(session: AsyncSession, event_id: str) -> bool:
    """尝试认领一个事件 ID。返回 True 表示这是第一次见到（已插入，调用方应继续处理），
    返回 False 表示之前已经处理过（`INSERT ... ON CONFLICT DO NOTHING` 未插入新行）。

    用 `ON CONFLICT DO NOTHING` 而非"先 SELECT 再 INSERT"：两条语句之间存在竞态窗口，
    飞书的快速重试可能在窗口内到达，`ON CONFLICT` 把判断收进单条原子语句。
    """
    result = await session.execute(
        text(
            "INSERT INTO ops.ops_dedup_key (event_id) VALUES (:event_id) "
            "ON CONFLICT (event_id) DO NOTHING"
        ),
        {"event_id": event_id},
    )
    return result.rowcount > 0


async def record_backup(
    session: AsyncSession,
    *,
    status: str,
    object_key: str | None,
    size_bytes: int | None,
    failed_stage: str | None,
) -> None:
    session.add(
        OpsBackupRecord(
            status=status,
            object_key=object_key,
            size_bytes=size_bytes,
            failed_stage=failed_stage,
            finished_at=func.now(),
        )
    )


async def latest_succeeded_backup(session: AsyncSession) -> OpsBackupRecord | None:
    result = await session.execute(
        select(OpsBackupRecord)
        .where(OpsBackupRecord.status == "SUCCEEDED")
        .order_by(OpsBackupRecord.finished_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def recent_tasks(session: AsyncSession, limit: int = 10) -> list[OpsTask]:
    """最近的运维任务记录，按创建时间倒序——供飞书"任务查询"指令使用。

    `limit` 由调用方给死值（指令处理层不接受用户传参调大它），这是"有界查询"
    约束落在这一层的体现：不因为查询接口存在就意味着可以无界。
    """
    result = await session.execute(select(OpsTask).order_by(OpsTask.created_at.desc()).limit(limit))
    return list(result.scalars())
