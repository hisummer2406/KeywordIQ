"""IP 专家定时复检（change add-opportunity-ip-agent 决策 13）。

- 每个调研至多一条；检索式必须是本调研**已成功执行过**的——定时任务只重复人确认过、语法已验证的
  检索式，不绕过人工确认。
- 到点由 IP 轮询器的维护性一轮处理：先把 ``next_run_at`` 推到下一个周期（错过的不补跑），再走
  手动检索同一条入队路径（配置、在途互斥、预算咨询锁）。被拒时本次记为「已跳过」并写明原因，
  不重试、不排队等待。令牌由定时任务 id 与本次到期时刻构成，重复处理不会重复入队。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import APIError
from app.table.opportunity import research_service
from app.table.opportunity.ip_errors import ScheduleNotFoundError, ScheduleQueryNotExecutedError
from app.table.opportunity.ip_model import OppIpQuery, OppIpResearch, OppIpSchedule
from app.table.opportunity.ip_schemas import ScheduleUpsert
from app.table.opportunity.research_service import schedule_out
from database import AsyncSessionFactory

logger = logging.getLogger(__name__)

_TZ = ZoneInfo("Asia/Shanghai")
#: 一轮最多处理多少条到期的定时任务
MAX_DUE_PER_ROUND = 20


def _now() -> datetime:
    return datetime.now(UTC)


def next_run(after: datetime, *, frequency: str, weekday: int | None, day_of_month: int | None,
             hour: int) -> datetime:
    """``after`` 之后（严格大于）的下一个执行时刻，按 Asia/Shanghai 计算，返回带时区的时间。"""
    local = after.astimezone(_TZ)
    if frequency == "weekly":
        assert weekday is not None
        day = local.date() + timedelta(days=(weekday - local.weekday()) % 7)
        cand = datetime(day.year, day.month, day.day, hour, tzinfo=_TZ)
        if cand <= local:
            cand += timedelta(days=7)
        return cand
    assert day_of_month is not None
    year, month = local.year, local.month
    cand = datetime(year, month, day_of_month, hour, tzinfo=_TZ)
    if cand <= local:
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        cand = datetime(year, month, day_of_month, hour, tzinfo=_TZ)
    return cand


def _next(s: OppIpSchedule | ScheduleUpsert, after: datetime) -> datetime:
    return next_run(after, frequency=s.frequency, weekday=s.weekday, day_of_month=s.day_of_month, hour=s.hour)


# ---------------------------------------------------------------- 增删改查


async def list_schedules(db: AsyncSession, owner_id: int) -> dict[str, Any]:
    rows = (await db.execute(
        select(OppIpSchedule, OppIpResearch.product_name, OppIpResearch.workspace_id)
        .join(OppIpResearch, OppIpResearch.id == OppIpSchedule.research_id)
        .where(OppIpResearch.owner_id == owner_id)
        .order_by(OppIpSchedule.enabled.desc(), OppIpSchedule.next_run_at, OppIpSchedule.id)
        .limit(500))).all()
    return {"items": [{**schedule_out(s), "product_name": name, "workspace_id": ws} for s, name, ws in rows]}


async def upsert_schedule(db: AsyncSession, owner_id: int, research_id: int, req: ScheduleUpsert) -> dict[str, Any]:
    r = await research_service._research(db, owner_id, research_id, lock=True)
    executed = set((await db.execute(select(OppIpQuery.query_text).where(
        OppIpQuery.research_id == r.id, OppIpQuery.status == "success"))).scalars().all())
    queries = [{"query_text": q.query_text, "limit": q.limit} for q in req.queries]
    if any(q["query_text"] not in executed for q in queries):
        raise ScheduleQueryNotExecutedError(log_detail=f"research={r.id}")
    s = (await db.execute(select(OppIpSchedule).where(OppIpSchedule.research_id == r.id))).scalar_one_or_none()
    if s is None:
        s = OppIpSchedule(research_id=r.id)
        db.add(s)
    s.frequency, s.weekday, s.day_of_month, s.hour = req.frequency, req.weekday, req.day_of_month, req.hour
    s.queries, s.enabled = queries, req.enabled
    # 任何修改（含恢复启用）都从现在起重算，不补跑暂停期间错过的周期
    s.next_run_at = _next(req, _now())
    s.updated_at = func.now()
    await db.commit()
    await db.refresh(s)
    return schedule_out(s)


async def delete_schedule(db: AsyncSession, owner_id: int, research_id: int) -> None:
    r = await research_service._research(db, owner_id, research_id, lock=True)
    s = (await db.execute(select(OppIpSchedule).where(OppIpSchedule.research_id == r.id))).scalar_one_or_none()
    if s is None:
        raise ScheduleNotFoundError(log_detail=f"research={r.id}")
    await db.delete(s)
    await db.commit()


# ---------------------------------------------------------------- 到期处理


async def _claim_due(session: AsyncSession, now: datetime) -> OppIpSchedule | None:
    return (await session.execute(
        select(OppIpSchedule).where(OppIpSchedule.enabled.is_(True), OppIpSchedule.next_run_at <= now)
        .order_by(OppIpSchedule.next_run_at, OppIpSchedule.id).limit(1).with_for_update(skip_locked=True)
    )).scalar_one_or_none()


def _token(schedule_id: int, due: datetime) -> str:
    return f"sched-{schedule_id}-{int(due.timestamp())}"


async def run_one_due(session: AsyncSession, now: datetime | None = None) -> bool:
    """处理一条到期的定时任务。返回 False 表示没有到期的。"""
    now = now or _now()
    s = await _claim_due(session, now)
    if s is None:
        return False
    sid, due, queries = s.id, s.next_run_at, list(s.queries)
    following = _next(s, now)
    r = await session.get(OppIpResearch, s.research_id)
    s.next_run_at, s.last_run_at, s.updated_at = following, now, now
    try:
        # 与手动检索同一条入队路径；成功时它会提交（连同上面对 s 的修改）
        res = await research_service.enqueue_search(session, r, queries, _token(sid, due), schedule_id=sid)
    except APIError as e:
        # 预算不足、在途任务、未配置：本次跳过并记录原因，不重试
        await session.rollback()
        s = await session.get(OppIpSchedule, sid, with_for_update=True, populate_existing=True)
        if s is not None:
            s.next_run_at, s.last_run_at, s.updated_at = following, now, now
            s.last_status, s.last_reason, s.last_task_id = "skipped", e.message, None
            await session.commit()
        logger.info("定时复检 %s 已跳过：%s", sid, e.code)
        return True
    s = await session.get(OppIpSchedule, sid, populate_existing=True)
    if s is not None:
        s.last_status, s.last_reason, s.last_task_id = "queued", None, res["task_id"]
        await session.commit()
    return True


async def run_due(session_factory=AsyncSessionFactory, now: datetime | None = None) -> int:
    n = 0
    for _ in range(MAX_DUE_PER_ROUND):
        async with session_factory() as session:
            if not await run_one_due(session, now):
                break
        n += 1
    return n


async def run_ip_schedules_round() -> bool:
    """维护性任务：恒返回 False，不参与「整轮重来」判定（见 app/jobs/poller.py）。"""
    try:
        await run_due()
    except Exception:
        logger.exception("IP 专家定时复检轮询出错")
    return False
