"""专利调研、付费动作入队与人工意见（change add-opportunity-ip-agent）。

1. **私有性在每条查询里**：所有读写都带 ``owner_id``，取不到一律 404。
2. **请求路径从不调用外部服务**：计划、检索、取证、分析只写任务行（202），由 ``task_service`` 执行。
3. **预算在入队时核对**（决策 4）：咨询锁内统计本调研与今日全站的 MCP 调用（留痕 + 在途计划）。
4. **幂等**：客户端令牌 + 输入指纹，同令牌不同输入 409。同一调研同时至多一个在途任务。
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import exists, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.site_registry import SITE_CATALOG
from app.table.opportunity.agent_prompts import MAX_QUERIES
from app.table.opportunity.ip_errors import (
    BudgetExceededError,
    CandidateNotFoundError,
    ImageNotFoundError,
    InvalidResearchInputError,
    LlmNotConfiguredError,
    McpNotConfiguredError,
    NothingToAnalyzeError,
    NothingToFetchError,
    ResearchNotFoundError,
    WorkspaceNotFoundError,
)
from app.table.opportunity.ip_model import (
    ACTIVE_TASK_STATUSES,
    DETAIL_TOOLS,
    OppIpAgentOutput,
    OppIpCall,
    OppIpCandidate,
    OppIpDetail,
    OppIpMessage,
    OppIpQuery,
    OppIpResearch,
    OppIpReview,
    OppIpSchedule,
    OppIpTask,
    OppWorkspace,
)
from app.table.opportunity.ip_schemas import (
    MAX_DETAIL_BATCH,
    QUERY_LIMITS,
    DetailRequest,
    MessageCreate,
    ResearchCreate,
    SearchRequest,
)
from app.table.opportunity.llm_client import llm_configured
from app.table.risk.check_service import fingerprint
from app.table.risk.jurisdictions import JURISDICTIONS, MARKET_RECOMMENDATIONS
from app.table.risk.review_rules import validate_review
from app.table.risk.risk_errors import TaskInProgressError, TokenConflictError
from app.table.risk.risk_schemas import ReviewCreate
from app.user.user_model import UserCenter
from config import settings

#: 预算核对的咨询锁（仓库 0x4Bxx 约定：0x4B49 导入、0x4B44 独立站认领、0x4B4F 运维）。
BUDGET_LOCK_KEY = 0x4B41
#: 详情里带回的最近对话条数
MESSAGE_WINDOW = 200
_TZ = ZoneInfo("Asia/Shanghai")


def mcp_configured() -> bool:
    return bool(settings.ZHIHUIYA_API_KEY)


def _today_start() -> datetime:
    return datetime.combine(datetime.now(_TZ).date(), time.min, tzinfo=_TZ)


# ---------------------------------------------------------------- 元数据


async def meta(db: AsyncSession) -> dict[str, Any]:
    return {
        "mcp_configured": mcp_configured(),
        "llm_configured": llm_configured(),
        "llm_model": settings.OPPORTUNITY_LLM_MODEL if llm_configured() else None,
        "markets": [{"code": s.code, "name": s.name_zh} for s in SITE_CATALOG.values()],
        "jurisdictions": [{"code": j.code, "name": j.name_zh, "note": j.note} for j in JURISDICTIONS.values()],
        "recommendations": {m: list(js) for m, js in MARKET_RECOMMENDATIONS.items()},
        "query_limits": list(QUERY_LIMITS),
        "max_queries": MAX_QUERIES,
        "max_detail_batch": MAX_DETAIL_BATCH,
        "detail_tools": list(DETAIL_TOOLS),
        "budget": {
            "per_research": settings.OPPORTUNITY_IP_MCP_BUDGET_PER_RESEARCH,
            "daily_limit": settings.OPPORTUNITY_IP_MCP_DAILY_LIMIT,
            "daily_used": await _daily_used(db),
        },
    }


# ---------------------------------------------------------------- 调研


async def _research(db: AsyncSession, owner_id: int, research_id: int, *, lock: bool = False) -> OppIpResearch:
    stmt = select(OppIpResearch).where(OppIpResearch.id == research_id, OppIpResearch.owner_id == owner_id)
    if lock:
        stmt = stmt.with_for_update()
    r = (await db.execute(stmt)).scalar_one_or_none()
    if r is None:
        raise ResearchNotFoundError(log_detail=f"research={research_id} owner={owner_id}")
    return r


async def create_research(db: AsyncSession, owner_id: int, req: ResearchCreate) -> dict[str, Any]:
    bad_m = [m for m in req.markets if m not in SITE_CATALOG]
    if bad_m:
        raise InvalidResearchInputError(f"未知销售市场：{', '.join(bad_m)}")
    bad_j = [j for j in req.jurisdictions if j not in JURISDICTIONS]
    if bad_j:
        raise InvalidResearchInputError(f"不支持的检索法域：{', '.join(bad_j)}")
    fp = fingerprint(req.model_dump(exclude={"client_token", "workspace_id"}))
    existing = await _research_by_token(db, owner_id, req.client_token)
    if existing is not None:
        return _same_or_conflict(existing, fp)
    if req.workspace_id is not None:
        await owned_workspace(db, owner_id, req.workspace_id)
    r = OppIpResearch(
        owner_id=owner_id, product_name=req.product_name, description=req.description,
        keywords=req.keywords, loc_classes=req.loc_classes, jurisdictions=req.jurisdictions,
        markets=req.markets, workspace_id=req.workspace_id, client_token=req.client_token, input_fingerprint=fp,
    )
    db.add(r)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await _research_by_token(db, owner_id, req.client_token)
        if existing is None:
            raise
        return _same_or_conflict(existing, fp)
    return {"id": r.id}


async def owned_workspace(db: AsyncSession, owner_id: int, workspace_id: int) -> OppWorkspace:
    ws = (await db.execute(select(OppWorkspace).where(
        OppWorkspace.id == workspace_id, OppWorkspace.owner_id == owner_id))).scalar_one_or_none()
    if ws is None:
        raise WorkspaceNotFoundError(log_detail=f"workspace={workspace_id} owner={owner_id}")
    return ws


async def move_research(db: AsyncSession, owner_id: int, research_id: int, workspace_id: int | None) -> dict[str, Any]:
    """移入工作空间或移出为未归类（决策 11）：组织方式，不改调研输入。"""
    r = await _research(db, owner_id, research_id, lock=True)
    if workspace_id is not None:
        await owned_workspace(db, owner_id, workspace_id)
    r.workspace_id = workspace_id
    await db.commit()
    return {"id": r.id, "workspace_id": r.workspace_id}


async def _research_by_token(db: AsyncSession, owner_id: int, token: str) -> OppIpResearch | None:
    return (await db.execute(select(OppIpResearch).where(
        OppIpResearch.owner_id == owner_id, OppIpResearch.client_token == token))).scalar_one_or_none()


def _same_or_conflict(r: OppIpResearch, fp: str) -> dict[str, Any]:
    if r.input_fingerprint != fp:
        raise TokenConflictError(log_detail=f"research={r.id}")
    return {"id": r.id}


async def list_researches(db: AsyncSession, owner_id: int, *, page: int, page_size: int,
                          workspace_id: int | None = None, unassigned: bool = False) -> dict[str, Any]:
    conds = [OppIpResearch.owner_id == owner_id]
    if unassigned:
        conds.append(OppIpResearch.workspace_id.is_(None))
    elif workspace_id is not None:
        conds.append(OppIpResearch.workspace_id == workspace_id)
    total = (await db.execute(select(func.count()).select_from(OppIpResearch).where(*conds))).scalar_one()
    candidates = (select(func.count()).where(OppIpCandidate.research_id == OppIpResearch.id)
                  .correlate(OppIpResearch).scalar_subquery())
    reviewed = (select(func.count(func.distinct(OppIpReview.market)))
                .where(OppIpReview.research_id == OppIpResearch.id).correlate(OppIpResearch).scalar_subquery())
    last_task = (select(OppIpTask.status).where(OppIpTask.research_id == OppIpResearch.id)
                 .order_by(OppIpTask.id.desc()).limit(1).correlate(OppIpResearch).scalar_subquery())
    last_kind = (select(OppIpTask.kind).where(OppIpTask.research_id == OppIpResearch.id)
                 .order_by(OppIpTask.id.desc()).limit(1).correlate(OppIpResearch).scalar_subquery())
    scheduled = (select(OppIpSchedule.enabled).where(OppIpSchedule.research_id == OppIpResearch.id)
                 .correlate(OppIpResearch).scalar_subquery())
    rows = (await db.execute(
        select(OppIpResearch, candidates.label("c"), reviewed.label("rv"), last_task.label("ts"),
               last_kind.label("tk"), scheduled.label("sc"))
        .where(*conds).order_by(OppIpResearch.created_at.desc(), OppIpResearch.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).all()
    return {
        "total": total, "page": page, "page_size": page_size,
        "items": [
            {"id": r.id, "product_name": r.product_name, "markets": r.markets, "jurisdictions": r.jurisdictions,
             "workspace_id": r.workspace_id, "created_at": r.created_at, "candidate_count": int(c or 0),
             "reviewed_markets": int(rv or 0), "last_task": {"kind": tk, "status": ts} if ts else None,
             # None = 没有定时复检；True/False = 启用/暂停
             "schedule_enabled": sc}
            for r, c, rv, ts, tk, sc in rows
        ],
    }


async def get_research(db: AsyncSession, owner_id: int, research_id: int) -> dict[str, Any]:
    r = await _research(db, owner_id, research_id)
    tasks = (await db.execute(select(OppIpTask).where(OppIpTask.research_id == r.id)
                              .order_by(OppIpTask.id.desc()).limit(50))).scalars().all()
    queries = (await db.execute(select(OppIpQuery).where(OppIpQuery.research_id == r.id)
                                .order_by(OppIpQuery.id))).scalars().all()
    candidates = (await db.execute(select(OppIpCandidate).where(OppIpCandidate.research_id == r.id)
                                   .order_by(OppIpCandidate.query_id, OppIpCandidate.rank))).scalars().all()
    details = (await db.execute(
        select(OppIpDetail.candidate_id, OppIpDetail.tool, OppIpDetail.status, OppIpDetail.error_category,
               OppIpDetail.data, OppIpDetail.fetched_at, (OppIpDetail.image.is_not(None)).label("has_image"))
        .join(OppIpCandidate, OppIpCandidate.id == OppIpDetail.candidate_id)
        .where(OppIpCandidate.research_id == r.id))).all()
    outputs = (await db.execute(select(OppIpAgentOutput).where(OppIpAgentOutput.research_id == r.id)
                                .order_by(OppIpAgentOutput.id.desc()).limit(20))).scalars().all()
    reviews = (await db.execute(select(OppIpReview).where(OppIpReview.research_id == r.id)
                                .order_by(OppIpReview.id.desc()))).scalars().all()
    calls = (await db.execute(
        select(OppIpCall.provider, OppIpCall.tool, OppIpCall.outcome, func.count())
        .where(OppIpCall.research_id == r.id)
        .group_by(OppIpCall.provider, OppIpCall.tool, OppIpCall.outcome))).all()
    messages = (await db.execute(select(OppIpMessage).where(OppIpMessage.research_id == r.id)
                                 .order_by(OppIpMessage.id.desc()).limit(MESSAGE_WINDOW))).scalars().all()
    schedule = (await db.execute(select(OppIpSchedule).where(OppIpSchedule.research_id == r.id))).scalar_one_or_none()
    names = await _user_names(db, {rv.signer_id for rv in reviews})

    by_cand: dict[int, dict[str, Any]] = {}
    for cid, tool, status, cat, data, fetched_at, has_image in details:
        by_cand.setdefault(cid, {})[tool] = {
            "status": status, "error_category": cat, "fetched_at": fetched_at,
            "data": (data or {}).get("normalized") if status == "success" else None,
            "has_image": bool(has_image),
        }
    reviews_by_market: dict[str, list[dict[str, Any]]] = {m: [] for m in r.markets}
    for rv in reviews:
        reviews_by_market.setdefault(rv.market, []).append(_review_out(rv, names))
    active = next((t for t in tasks if t.status in ACTIVE_TASK_STATUSES), None)
    used = sum(n for p, _, _, n in calls if p == "mcp")

    return {
        "id": r.id, "product_name": r.product_name, "description": r.description, "keywords": r.keywords,
        "loc_classes": r.loc_classes, "jurisdictions": r.jurisdictions, "markets": r.markets,
        "workspace_id": r.workspace_id, "created_at": r.created_at,
        "jurisdiction_details": [
            {"code": j, "name": JURISDICTIONS[j].name_zh if j in JURISDICTIONS else j,
             "note": JURISDICTIONS[j].note if j in JURISDICTIONS else ""}
            for j in r.jurisdictions
        ],
        "mcp_configured": mcp_configured(), "llm_configured": llm_configured(),
        "budget": {"per_research": settings.OPPORTUNITY_IP_MCP_BUDGET_PER_RESEARCH, "used": used,
                   "daily_limit": settings.OPPORTUNITY_IP_MCP_DAILY_LIMIT, "daily_used": await _daily_used(db)},
        "active_task": _task_out(active) if active else None,
        "tasks": [_task_out(t) for t in tasks],
        "plans": [_output_out(o) for o in outputs if o.kind == "plan"],
        "analyses": [_output_out(o) for o in outputs if o.kind == "analysis"],
        "queries": [
            {"id": q.id, "task_id": q.task_id, "query_text": q.query_text, "limit": q.limit, "status": q.status,
             "error_category": q.error_category, "total_count": q.total_count,
             "returned_count": q.returned_count, "fetched_at": q.fetched_at}
            for q in queries
        ],
        "candidates": [
            {"id": c.id, "query_id": c.query_id, "rank": c.rank, "patent_id": c.patent_id, "pn": c.pn,
             "title": c.title, "authority": c.authority, "apno": c.apno, "apdt": c.apdt, "pbdt": c.pbdt,
             "assignee": c.assignee, "fetched_at": c.fetched_at, "details": by_cand.get(c.id, {})}
            for c in candidates
        ],
        "coverage": coverage_facts(queries, candidates, by_cand),
        "search_done": any(q.status == "success" for q in queries),
        "reviews": reviews_by_market,
        "calls": [{"provider": p, "tool": t, "outcome": o, "count": n} for p, t, o, n in calls],
        "messages": [message_out(m) for m in reversed(messages)],
        "schedule": schedule_out(schedule) if schedule else None,
    }


def coverage_facts(queries, candidates, by_cand: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """服务端算出的覆盖事实，和 Agent 分析并排展示（决策 7），不由模型生成。"""
    st = [q.status if hasattr(q, "status") else q["status"] for q in queries]
    return {
        "queries_total": len(st),
        "queries_success": st.count("success"),
        "queries_failed": st.count("failed"),
        "queries_unknown": st.count("unknown"),
        "candidates": len(candidates),
        "detailed": {tool: sum(1 for d in by_cand.values() if (d.get(tool) or {}).get("status") == "success")
                     for tool in DETAIL_TOOLS},
    }


def _task_out(t: OppIpTask) -> dict[str, Any]:
    return {"id": t.id, "kind": t.kind, "status": t.status, "error_category": t.error_category,
            "planned_mcp_calls": t.planned_mcp_calls, "scheduled": bool((t.params or {}).get("schedule_id")),
            "created_at": t.created_at, "started_at": t.started_at, "finished_at": t.finished_at}


def message_out(m: OppIpMessage) -> dict[str, Any]:
    return {"id": m.id, "task_id": m.task_id, "role": m.role, "content": m.content, "model": m.model,
            "cited_candidate_ids": m.cited_candidate_ids, "created_at": m.created_at}


def schedule_out(s: OppIpSchedule) -> dict[str, Any]:
    return {"id": s.id, "research_id": s.research_id, "frequency": s.frequency, "weekday": s.weekday,
            "day_of_month": s.day_of_month, "hour": s.hour, "queries": s.queries, "enabled": s.enabled,
            "next_run_at": s.next_run_at, "last_run_at": s.last_run_at, "last_status": s.last_status,
            "last_reason": s.last_reason, "last_task_id": s.last_task_id, "created_at": s.created_at,
            "updated_at": s.updated_at}


def _output_out(o: OppIpAgentOutput) -> dict[str, Any]:
    return {"id": o.id, "task_id": o.task_id, "model": o.model, "content": o.content,
            "coverage": o.coverage, "created_at": o.created_at}


async def _user_names(db: AsyncSession, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = (await db.execute(select(UserCenter.id, UserCenter.display_name, UserCenter.user_name)
                             .where(UserCenter.id.in_(ids)))).all()
    return {i: d or n for i, d, n in rows}


def _review_out(r: OppIpReview, names: dict[int, str]) -> dict[str, Any]:
    return {
        "id": r.id, "market": r.market, "risk_level": r.risk_level, "decision": r.decision,
        "basis": r.basis, "uncovered_scope": r.uncovered_scope, "conditions": r.conditions,
        "evidence_candidate_ids": r.evidence_candidate_ids, "review_date": r.review_date,
        "signer_id": r.signer_id, "signer_name": names.get(r.signer_id), "created_at": r.created_at,
    }


async def get_candidate_image(db: AsyncSession, owner_id: int, research_id: int,
                              candidate_id: int) -> tuple[bytes, str]:
    await _research(db, owner_id, research_id)
    row = (await db.execute(
        select(OppIpDetail.image, OppIpDetail.image_content_type)
        .join(OppIpCandidate, OppIpCandidate.id == OppIpDetail.candidate_id)
        .where(OppIpCandidate.research_id == research_id, OppIpCandidate.id == candidate_id,
               OppIpDetail.tool == "abstract_image"))).first()
    if row is None or row[0] is None:
        raise ImageNotFoundError(log_detail=f"research={research_id} candidate={candidate_id}")
    return row[0], row[1] or "image/png"


# ---------------------------------------------------------------- 预算（决策 4）


async def _daily_used(db: AsyncSession) -> int:
    return (await db.execute(select(func.count()).select_from(OppIpCall).where(
        OppIpCall.provider == "mcp", OppIpCall.started_at >= _today_start()))).scalar_one()


async def _check_budget(db: AsyncSession, research_id: int, needed: int) -> None:
    """在当前事务内持锁核对；锁随提交释放，任务行与核对在同一事务里落库。"""
    await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": BUDGET_LOCK_KEY})
    planned = OppIpTask.status.in_(ACTIVE_TASK_STATUSES)
    r_used = (await db.execute(select(func.count()).select_from(OppIpCall).where(
        OppIpCall.research_id == research_id, OppIpCall.provider == "mcp"))).scalar_one()
    r_planned = (await db.execute(select(func.coalesce(func.sum(OppIpTask.planned_mcp_calls), 0)).where(
        OppIpTask.research_id == research_id, planned))).scalar_one()
    limit = settings.OPPORTUNITY_IP_MCP_BUDGET_PER_RESEARCH
    if r_used + r_planned + needed > limit:
        raise BudgetExceededError(
            f"本调研 MCP 调用预算不足：上限 {limit}，已用 {r_used + r_planned}，本次需要 {needed}")
    d_used = await _daily_used(db)
    d_planned = (await db.execute(select(func.coalesce(func.sum(OppIpTask.planned_mcp_calls), 0))
                                  .where(planned))).scalar_one()
    daily = settings.OPPORTUNITY_IP_MCP_DAILY_LIMIT
    if d_used + d_planned + needed > daily:
        raise BudgetExceededError(
            f"今日全站 MCP 调用已达上限：上限 {daily}，已用 {d_used + d_planned}，本次需要 {needed}")


# ---------------------------------------------------------------- 付费动作入队


async def _existing_task(db: AsyncSession, research_id: int, token: str) -> OppIpTask | None:
    return (await db.execute(select(OppIpTask).where(
        OppIpTask.research_id == research_id, OppIpTask.client_token == token))).scalar_one_or_none()


def _task_same_or_conflict(task: OppIpTask, fp: str) -> dict[str, Any]:
    if task.input_fingerprint != fp:
        raise TokenConflictError(log_detail=f"task={task.id}")
    return {"task_id": task.id, "status": task.status}


async def _busy(db: AsyncSession, research_id: int) -> bool:
    return bool((await db.execute(select(exists().where(
        OppIpTask.research_id == research_id, OppIpTask.status.in_(ACTIVE_TASK_STATUSES))))).scalar())


async def _enqueue(db: AsyncSession, r: OppIpResearch, *, kind: str, params: dict[str, Any], token: str,
                   fp: str, mcp_calls: int = 0, message: str | None = None) -> dict[str, Any]:
    """令牌查重 → 在途互斥 → 预算 → 落任务行。调用方已检查配置与业务前提。

    ``message``：追问正文，与任务行同一事务写入（决策 12）——在途冲突时消息也不落库。
    """
    existing = await _existing_task(db, r.id, token)
    if existing is not None:
        return _task_same_or_conflict(existing, fp)
    if await _busy(db, r.id):
        raise TaskInProgressError("已有任务在执行，请等待完成")
    if mcp_calls:
        await _check_budget(db, r.id, mcp_calls)
    task = OppIpTask(research_id=r.id, kind=kind, status="queued", params=params,
                     planned_mcp_calls=mcp_calls, client_token=token, input_fingerprint=fp)
    db.add(task)
    try:
        if message is not None:
            await db.flush()
            db.add(OppIpMessage(research_id=r.id, task_id=task.id, role="user", content=message))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await _existing_task(db, r.id, token)
        if existing is not None:
            return _task_same_or_conflict(existing, fp)
        raise TaskInProgressError("已有任务在执行，请等待完成", log_detail=f"research={r.id}") from None
    return {"task_id": task.id, "status": task.status}


async def request_plan(db: AsyncSession, owner_id: int, research_id: int, token: str) -> dict[str, Any]:
    r = await _research(db, owner_id, research_id, lock=True)
    fp = fingerprint({"kind": "plan"})
    if (existing := await _existing_task(db, r.id, token)) is not None:
        return _task_same_or_conflict(existing, fp)
    if not llm_configured():
        raise LlmNotConfiguredError()
    return await _enqueue(db, r, kind="plan", params={}, token=token, fp=fp)


async def request_search(db: AsyncSession, owner_id: int, research_id: int, req: SearchRequest) -> dict[str, Any]:
    r = await _research(db, owner_id, research_id, lock=True)
    queries = [{"query_text": q.query_text, "limit": q.limit} for q in req.queries]
    return await enqueue_search(db, r, queries, req.client_token)


async def enqueue_search(db: AsyncSession, r: OppIpResearch, queries: list[dict[str, Any]], token: str, *,
                         schedule_id: int | None = None) -> dict[str, Any]:
    """手动检索与定时复检共用的入队路径：同一套配置检查、在途互斥与预算核对（决策 13）。"""
    fp = fingerprint({"kind": "search", "queries": queries})
    if (existing := await _existing_task(db, r.id, token)) is not None:
        return _task_same_or_conflict(existing, fp)
    if not mcp_configured():
        raise McpNotConfiguredError()
    params: dict[str, Any] = {"queries": queries}
    if schedule_id is not None:
        params["schedule_id"] = schedule_id
    return await _enqueue(db, r, kind="search", params=params, token=token, fp=fp, mcp_calls=len(queries))


async def request_details(db: AsyncSession, owner_id: int, research_id: int, req: DetailRequest) -> dict[str, Any]:
    r = await _research(db, owner_id, research_id, lock=True)
    fp = fingerprint({"kind": "detail", "ids": req.candidate_ids, "tools": req.tools})
    if (existing := await _existing_task(db, r.id, req.client_token)) is not None:
        return _task_same_or_conflict(existing, fp)
    owned = (await db.execute(select(func.count()).select_from(OppIpCandidate).where(
        OppIpCandidate.research_id == r.id, OppIpCandidate.id.in_(req.candidate_ids)))).scalar_one()
    if owned != len(req.candidate_ids):
        raise CandidateNotFoundError(log_detail=f"research={r.id}")
    done = set((await db.execute(select(OppIpDetail.candidate_id, OppIpDetail.tool).where(
        OppIpDetail.candidate_id.in_(req.candidate_ids), OppIpDetail.tool.in_(req.tools),
        OppIpDetail.status == "success"))).all())
    needed = sum(1 for c in req.candidate_ids for t in req.tools if (c, t) not in done)
    if needed == 0:
        raise NothingToFetchError()
    if not mcp_configured():
        raise McpNotConfiguredError()
    return await _enqueue(db, r, kind="detail", params={"candidate_ids": req.candidate_ids, "tools": req.tools},
                          token=req.client_token, fp=fp, mcp_calls=needed)


async def request_analysis(db: AsyncSession, owner_id: int, research_id: int, token: str) -> dict[str, Any]:
    r = await _research(db, owner_id, research_id, lock=True)
    fp = fingerprint({"kind": "analyze"})
    if (existing := await _existing_task(db, r.id, token)) is not None:
        return _task_same_or_conflict(existing, fp)
    if not llm_configured():
        raise LlmNotConfiguredError()
    searched = (await db.execute(select(exists().where(
        OppIpQuery.research_id == r.id, OppIpQuery.status == "success")))).scalar()
    if not searched:
        raise NothingToAnalyzeError()
    return await _enqueue(db, r, kind="analyze", params={}, token=token, fp=fp)


async def request_ask(db: AsyncSession, owner_id: int, research_id: int, req: MessageCreate) -> dict[str, Any]:
    """对话追问（决策 12）：只调用大模型，不调用 MCP、不计 MCP 预算。"""
    r = await _research(db, owner_id, research_id, lock=True)
    fp = fingerprint({"kind": "ask", "content": req.content})
    if (existing := await _existing_task(db, r.id, req.client_token)) is not None:
        return _task_same_or_conflict(existing, fp)
    if not llm_configured():
        raise LlmNotConfiguredError()
    return await _enqueue(db, r, kind="ask", params={}, token=req.client_token, fp=fp, message=req.content)


# ---------------------------------------------------------------- 人工意见（决策 8）


async def add_review(db: AsyncSession, owner_id: int, research_id: int, req: ReviewCreate) -> dict[str, Any]:
    r = await _research(db, owner_id, research_id, lock=True)
    fp = fingerprint(req.model_dump(exclude={"client_token"}))
    existing = (await db.execute(select(OppIpReview).where(
        OppIpReview.research_id == r.id, OppIpReview.client_token == req.client_token))).scalar_one_or_none()
    if existing is not None:
        if existing.input_fingerprint != fp:
            raise TokenConflictError(log_detail=f"review={existing.id}")
        return _review_out(existing, await _user_names(db, {existing.signer_id}))

    owned = len(req.evidence_candidate_ids)
    if req.evidence_candidate_ids:
        owned = (await db.execute(select(func.count()).select_from(OppIpCandidate).where(
            OppIpCandidate.research_id == r.id, OppIpCandidate.id.in_(req.evidence_candidate_ids)))).scalar_one()
    searched = (await db.execute(select(exists().where(
        OppIpQuery.research_id == r.id, OppIpQuery.status == "success")))).scalar()
    validate_review(req, markets=r.markets, evidence_owned=owned == len(req.evidence_candidate_ids),
                    search_done=bool(searched))

    review = OppIpReview(
        research_id=r.id, market=req.market, risk_level=req.risk_level, decision=req.decision,
        basis=req.basis, uncovered_scope=req.uncovered_scope, conditions=req.conditions,
        evidence_candidate_ids=req.evidence_candidate_ids, review_date=req.review_date,
        signer_id=owner_id, client_token=req.client_token, input_fingerprint=fp,
    )
    db.add(review)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return await add_review(db, owner_id, research_id, req)
    return _review_out(review, await _user_names(db, {owner_id}))
