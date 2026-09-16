"""ops schema 的数据访问层。纯数据库操作，不含业务判断（同构项目 `*_crud.py` 分层约定）。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ops_bot.model import TASK_FAILED, TASK_RUNNING, TASK_SUCCEEDED, OpsTask


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
