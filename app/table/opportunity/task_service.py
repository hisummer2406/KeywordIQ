"""IP 专家任务的认领、执行与收尾（change add-opportunity-ip-agent 决策 5、6、7）。

与 #24 ``risk/task_service.py`` 相同的三段式，**调用外部服务期间不持有任何数据库会话**：

1. ``_claim``：短事务里 ``FOR UPDATE SKIP LOCKED`` 取一个 queued 任务，改 running、写租约，
   把执行所需的一切抄成快照，提交。
2. ``_execute``：只拿快照调用 MCP / 大模型 / 附图下载，返回结果。
3. ``_finalize``：新事务写入调用留痕与证据，收尾任务状态。

租约过期由 ``reap_expired`` 改为 unknown，**不自动重发**——扣费可能已经发生。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.table.opportunity import agent_prompts as prompts
from app.table.opportunity.ip_model import (
    OppIpAgentOutput,
    OppIpCall,
    OppIpCandidate,
    OppIpDetail,
    OppIpMessage,
    OppIpQuery,
    OppIpResearch,
    OppIpTask,
)
from app.table.opportunity.llm_client import LlmClient, llm_configured, make_llm_client
from app.table.opportunity.mcp_client import BIBLIO, IMAGE, LEGAL, SEARCH, McpSession
from app.table.opportunity.research_service import coverage_facts
from app.table.risk.jurisdictions import JURISDICTIONS
from app.table.risk.provider_client import CallResult
from app.table.risk.task_service import _i, _norm_biblio, _s
from config import settings
from database import AsyncSessionFactory

logger = logging.getLogger(__name__)

NOT_CONFIGURED = "not_configured"
INTERRUPTED = "interrupted"
INVALID_OUTPUT = "invalid_output"
#: 摘要附图下载上限与允许的主机后缀（决策 6）
MAX_IMAGE_BYTES = 2 * 1024 * 1024
IMAGE_HOST_SUFFIX = ".zhihuiya.com"


def _now() -> datetime:
    return datetime.now(UTC)


def make_mcp_session() -> McpSession:
    """测试替换这个工厂注入 ``httpx.MockTransport``。"""
    return McpSession(settings.ZHIHUIYA_API_KEY, timeout=settings.OPPORTUNITY_IP_MCP_TIMEOUT_SECONDS)


#: 附图下载用的 transport；测试注入 MockTransport。
image_transport: httpx.AsyncBaseTransport | None = None


@dataclass
class Snapshot:
    task_id: int
    research_id: int
    kind: str
    params: dict[str, Any]
    research: dict[str, Any] = field(default_factory=dict)
    #: detail：[(candidate_id, patent_id, 需取的工具)]
    targets: list[tuple[int, str, list[str]]] = field(default_factory=list)
    #: analyze / ask：组装好的证据输入与可引用的候选 id
    analysis_input: dict[str, Any] = field(default_factory=dict)
    #: ask：用户的问题、最近对话与最新分析摘要（决策 12）
    question: str = ""
    history: list[dict[str, str]] = field(default_factory=list)
    latest_summary: str | None = None


@dataclass
class Outcome:
    calls: list[CallResult] = field(default_factory=list)
    #: search：[(query_text, limit, CallResult)]
    searches: list[tuple[str, int, CallResult]] = field(default_factory=list)
    #: detail：[(candidate_id, tool, CallResult, image bytes, image content type)]
    details: list[tuple[int, str, CallResult, bytes | None, str | None]] = field(default_factory=list)
    #: plan / analyze：校验后的输出，或失败分类
    output: dict[str, Any] | None = None
    output_error: str | None = None
    model: str | None = None


# ---------------------------------------------------------------- 认领


def _research_dict(r: OppIpResearch) -> dict[str, Any]:
    return {"product_name": r.product_name, "description": r.description, "keywords": r.keywords,
            "loc_classes": r.loc_classes, "jurisdictions": r.jurisdictions, "markets": r.markets}


def _configured(kind: str) -> bool:
    return bool(settings.ZHIHUIYA_API_KEY) if kind in ("search", "detail") else llm_configured()


async def _claim(session: AsyncSession) -> Snapshot | bool | None:
    """返回快照；没有任务返回 None；认领后因未配置直接失败返回 True。"""
    task = (await session.execute(
        select(OppIpTask).where(OppIpTask.status == "queued")
        .order_by(OppIpTask.created_at, OppIpTask.id).limit(1).with_for_update(skip_locked=True)
    )).scalar_one_or_none()
    if task is None:
        return None
    now = _now()
    if not _configured(task.kind):
        task.status, task.error_category = "failed", NOT_CONFIGURED
        task.started_at = task.finished_at = now
        await session.commit()
        return True

    r = await session.get(OppIpResearch, task.research_id)
    task.status, task.started_at = "running", now
    task.lease_until = now + timedelta(seconds=settings.OPPORTUNITY_IP_TASK_LEASE_SECONDS)
    snap = Snapshot(task_id=task.id, research_id=r.id, kind=task.kind, params=dict(task.params),
                    research=_research_dict(r))
    if task.kind == "detail":
        ids = [int(i) for i in task.params["candidate_ids"]]
        tools = list(task.params["tools"])
        cands = (await session.execute(select(OppIpCandidate.id, OppIpCandidate.patent_id).where(
            OppIpCandidate.research_id == r.id, OppIpCandidate.id.in_(ids)))).all()
        done = set((await session.execute(select(OppIpDetail.candidate_id, OppIpDetail.tool).where(
            OppIpDetail.candidate_id.in_(ids), OppIpDetail.status == "success"))).all())
        for cid, pid in cands:
            need = [t for t in tools if (cid, t) not in done]
            if need:
                snap.targets.append((cid, pid, need))
    elif task.kind == "analyze":
        snap.analysis_input = await _analysis_input(session, r.id)
    elif task.kind == "ask":
        snap.analysis_input = await _analysis_input(session, r.id)
        await _ask_context(session, snap)
    await session.commit()
    return snap


async def _ask_context(session: AsyncSession, snap: Snapshot) -> None:
    msgs = (await session.execute(
        select(OppIpMessage).where(OppIpMessage.research_id == snap.research_id)
        .order_by(OppIpMessage.id.desc()).limit(prompts.ASK_HISTORY + 1))).scalars().all()
    msgs = list(reversed(msgs))
    question = next((m for m in msgs if m.task_id == snap.task_id and m.role == "user"), None)
    snap.question = question.content if question else ""
    snap.history = [{"role": m.role, "content": m.content[:prompts.MAX_ITEM * 2]} for m in msgs if m is not question]
    latest = (await session.execute(
        select(OppIpAgentOutput.content).where(OppIpAgentOutput.research_id == snap.research_id,
                                               OppIpAgentOutput.kind == "analysis")
        .order_by(OppIpAgentOutput.id.desc()).limit(1))).scalar_one_or_none()
    snap.latest_summary = (latest or {}).get("summary")


async def _analysis_input(session: AsyncSession, research_id: int) -> dict[str, Any]:
    queries = (await session.execute(select(OppIpQuery).where(OppIpQuery.research_id == research_id)
                                     .order_by(OppIpQuery.id))).scalars().all()
    cands = (await session.execute(
        select(OppIpCandidate).where(OppIpCandidate.research_id == research_id)
        .order_by(OppIpCandidate.query_id, OppIpCandidate.rank).limit(prompts.MAX_ANALYSIS_CANDIDATES)
    )).scalars().all()
    details = (await session.execute(
        select(OppIpDetail.candidate_id, OppIpDetail.tool, OppIpDetail.status, OppIpDetail.data)
        .where(OppIpDetail.candidate_id.in_([c.id for c in cands]))
    )).all() if cands else []
    by_cand: dict[int, dict[str, Any]] = {}
    for cid, tool, status, data in details:
        by_cand.setdefault(cid, {})[tool] = {"status": status, "data": (data or {}).get("normalized")}

    def legal(cid: int) -> list[str] | None:
        d = by_cand.get(cid, {}).get(LEGAL)
        return (d["data"] or {}).get("simple_legal_status") if d and d["status"] == "success" else None

    def biblio(cid: int) -> dict[str, Any] | None:
        d = by_cand.get(cid, {}).get(BIBLIO)
        if not (d and d["status"] == "success" and d["data"]):
            return None
        b = d["data"]
        return {"patent_type": b.get("patent_type"), "loc": b.get("loc"), "assignees": b.get("assignees"),
                "publication_date": b.get("publication_date")}

    return {
        "queries": [{"query_text": q.query_text, "status": q.status, "error_category": q.error_category,
                     "total_count": q.total_count, "returned_count": q.returned_count} for q in queries],
        "candidates": [
            {"candidate_id": c.id, "pn": c.pn, "title": (c.title or "")[:300], "authority": c.authority,
             "apdt": c.apdt, "pbdt": c.pbdt, "assignee": c.assignee, "legal_status": legal(c.id),
             "bibliography": biblio(c.id),
             "has_abstract_image": (by_cand.get(c.id, {}).get(IMAGE) or {}).get("status") == "success"}
            for c in cands
        ],
        "candidate_ids": [c.id for c in cands],
        "coverage": coverage_facts(queries, cands, by_cand),
    }


# ---------------------------------------------------------------- 执行（不持有会话）


async def _execute(snap: Snapshot, mcp_factory: Callable[[], McpSession],
                   llm_factory: Callable[[], LlmClient]) -> Outcome:
    out = Outcome()
    if snap.kind == "search":
        async with mcp_factory() as mcp:
            for q in snap.params["queries"]:
                res = await mcp.call(SEARCH, {"query_text": q["query_text"], "limit": int(q["limit"]), "offset": 0})
                out.calls.append(res)
                out.searches.append((q["query_text"], int(q["limit"]), res))
        return out

    if snap.kind == "detail":
        async with mcp_factory() as mcp:
            for cid, pid, tools in snap.targets:
                for tool in tools:
                    res = await mcp.call(tool, {"patent_id": pid})
                    out.calls.append(res)
                    img, ctype = (None, None)
                    if tool == IMAGE and res.ok:
                        img, ctype = await _download_image(_image_path(res.data))
                    out.details.append((cid, tool, res, img, ctype))
        return out

    llm = llm_factory()
    out.model = llm.model
    if snap.kind == "plan":
        codes = [JURISDICTIONS[j].vendor_code for j in snap.research["jurisdictions"] if j in JURISDICTIONS]
        res = await llm.complete_json(prompts.PLAN_SYSTEM, prompts.plan_user_prompt(snap.research, codes))
        res.extra["tool"] = "plan"
        out.calls.append(res)
        if res.ok:
            try:
                out.output = prompts.validate_plan(res.data)
            except prompts.InvalidAgentOutput:
                out.output_error = INVALID_OUTPUT
        return out

    inp = snap.analysis_input
    if snap.kind == "ask":
        res = await llm.complete_json(prompts.ASK_SYSTEM, prompts.ask_user_prompt(
            snap.research, inp, snap.latest_summary, snap.history, snap.question))
        res.extra["tool"] = "ask"
        out.calls.append(res)
        if res.ok:
            try:
                out.output = prompts.validate_answer(res.data, set(inp["candidate_ids"]))
            except prompts.InvalidAgentOutput:
                out.output_error = INVALID_OUTPUT
        return out

    res = await llm.complete_json(prompts.ANALYSIS_SYSTEM, prompts.analysis_user_prompt(
        snap.research, inp["queries"], inp["candidates"], inp["coverage"]))
    res.extra["tool"] = "analysis"
    out.calls.append(res)
    if res.ok:
        try:
            out.output = prompts.validate_analysis(res.data, set(inp["candidate_ids"]))
        except prompts.InvalidAgentOutput:
            out.output_error = INVALID_OUTPUT
    return out


def _image_path(data: Any) -> str | None:
    if isinstance(data, list) and data and isinstance(data[0], dict):
        drawing = data[0].get("abstract_drawing")
        if isinstance(drawing, dict) and isinstance(drawing.get("path"), str):
            return drawing["path"]
    return None


def _allowed_image_url(url: str | None) -> bool:
    if not url:
        return False
    parts = urlsplit(url)
    host = parts.hostname or ""
    return parts.scheme == "https" and host.endswith(IMAGE_HOST_SUFFIX) and parts.port in (None, 443)


def _sniff(content: bytes) -> str | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


async def _download_image(url: str | None) -> tuple[bytes | None, str | None]:
    """下载失败一律返回 (None, None)：元数据照常保存，图片标记缺失（决策 6）。"""
    if not _allowed_image_url(url):
        return None, None
    try:
        async with httpx.AsyncClient(timeout=20, trust_env=False, follow_redirects=False,
                                     transport=image_transport) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    return None, None
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > MAX_IMAGE_BYTES:
                        return None, None
    except httpx.HTTPError:
        logger.warning("摘要附图下载失败")
        return None, None
    content = bytes(buf)
    ctype = _sniff(content)
    return (content, ctype) if ctype else (None, None)


# ---------------------------------------------------------------- 收尾


def _task_status(calls: list[CallResult]) -> tuple[str, str | None]:
    for c in calls:
        if c.outcome == "unknown":
            return "unknown", c.error_category
    for c in calls:
        if c.outcome == "error":
            return "failed", c.error_category
    return "completed", None


async def _finalize(session: AsyncSession, snap: Snapshot, out: Outcome) -> None:
    task = await session.get(OppIpTask, snap.task_id, with_for_update=True)
    provider = "mcp" if snap.kind in ("search", "detail") else "llm"
    # MCP 参数摘要：检索式 / patent_id，按调用顺序对应
    mcp_args: list[dict[str, Any]] = (
        [{"query_text": q, "limit": lim} for q, lim, _ in out.searches] if snap.kind == "search"
        else [{"candidate_id": cid} for cid, _, _, _, _ in out.details]
    )
    for i, c in enumerate(out.calls):
        if provider == "llm":
            args = {"model": out.model, "usage": c.extra.get("usage") or {}}
            tool = c.extra.get("tool", snap.kind)
        else:
            args, tool = (mcp_args[i] if i < len(mcp_args) else {}), c.api
        session.add(OppIpCall(
            research_id=snap.research_id, task_id=task.id, provider=provider, tool=tool, args=args,
            outcome=c.outcome, http_status=c.http_status, error_code=c.error_code,
            error_category=c.error_category, started_at=c.started_at, finished_at=c.finished_at,
        ))
    status, category = _task_status(out.calls)
    if not out.calls:
        status, category = "completed", None  # detail 全部已有成功结果

    if snap.kind == "search":
        await _save_searches(session, snap, out)
    elif snap.kind == "detail":
        await _save_details(session, snap, out)
    elif status == "completed":
        if out.output is None:
            status, category = "failed", out.output_error or INVALID_OUTPUT
        elif snap.kind == "ask":
            session.add(OppIpMessage(
                research_id=snap.research_id, task_id=task.id, role="agent", content=out.output["answer"],
                model=out.model, cited_candidate_ids=out.output["cited_candidate_ids"],
            ))
        else:
            session.add(OppIpAgentOutput(
                research_id=snap.research_id, task_id=task.id, kind="plan" if snap.kind == "plan" else "analysis",
                model=out.model or "", content=out.output,
                coverage=snap.analysis_input.get("coverage") if snap.kind == "analyze" else None,
            ))

    task.status, task.error_category = status, category
    task.finished_at, task.lease_until = _now(), None
    await session.commit()


async def _save_searches(session: AsyncSession, snap: Snapshot, out: Outcome) -> None:
    for query_text, limit, res in out.searches:
        if res.ok:
            items: list[dict[str, Any]] = res.data["results"]
            q = OppIpQuery(research_id=snap.research_id, task_id=snap.task_id, query_text=query_text, limit=limit,
                           status="success", total_count=int(res.data["total_search_result_count"]),
                           returned_count=len(items), raw=res.data, fetched_at=res.finished_at)
        else:
            items = []
            q = OppIpQuery(research_id=snap.research_id, task_id=snap.task_id, query_text=query_text, limit=limit,
                           status="unknown" if res.outcome == "unknown" else "failed",
                           error_category=res.error_category, fetched_at=res.finished_at)
        session.add(q)
        await session.flush()
        rows = [
            {"research_id": snap.research_id, "query_id": q.id, "rank": i + 1, "patent_id": it["patent_id"],
             "pn": _s(it.get("pn")), "title": _s(it.get("title")), "authority": _s(it.get("authority")),
             "apno": _s(it.get("apno")), "apdt": _i(it.get("apdt")), "pbdt": _i(it.get("pbdt")),
             "assignee": _s(it.get("current_assignee")) or _s(it.get("original_assignee")),
             "fetched_at": res.finished_at}
            for i, it in enumerate(items[:limit])
        ]
        if rows:
            await session.execute(pg_insert(OppIpCandidate).values(rows).on_conflict_do_nothing(
                index_elements=["research_id", "patent_id"]))


def _normalize(tool: str, item: dict[str, Any]) -> dict[str, Any]:
    if tool == LEGAL:
        v = item.get("simple_legal_status")
        legal = item.get("patent_legal") if isinstance(item.get("patent_legal"), dict) else {}
        v = v if isinstance(v, list) else legal.get("simple_legal_status")
        return {"pn": _s(item.get("pn")),
                "simple_legal_status": [x for x in v if isinstance(x, str)] if isinstance(v, list) else []}
    if tool == BIBLIO:
        return _norm_biblio(item)
    return {"pn": _s(item.get("pn"))}


def _strip_signed_urls(tool: str, item: dict[str, Any]) -> dict[str, Any]:
    """附图地址带签名且约 1 小时失效，不存进原始条目；图片本体另存。"""
    if tool != IMAGE:
        return item
    drawing = item.get("abstract_drawing")
    if isinstance(drawing, dict):
        return {**item, "abstract_drawing": {k: v for k, v in drawing.items() if k != "path"}}
    return item


async def _save_details(session: AsyncSession, snap: Snapshot, out: Outcome) -> None:
    for cid, tool, res, img, ctype in out.details:
        if res.ok:
            item = res.data[0] if res.data else None
            status = "success" if item is not None else "missing"
            data = ({"normalized": _normalize(tool, item), "raw": _strip_signed_urls(tool, item)}
                    if item is not None else None)
            category = None
        else:
            status = "unknown" if res.outcome == "unknown" else "failed"
            data, category, img, ctype = None, res.error_category, None, None
        stmt = pg_insert(OppIpDetail).values(
            candidate_id=cid, tool=tool, status=status, task_id=snap.task_id, error_category=category,
            data=data, image=img, image_content_type=ctype, fetched_at=res.finished_at,
        )
        await session.execute(stmt.on_conflict_do_update(
            index_elements=["candidate_id", "tool"],
            set_={k: getattr(stmt.excluded, k) for k in
                  ("status", "task_id", "error_category", "data", "image", "image_content_type", "fetched_at")},
            # 已成功的不覆盖（认领时已排除，这里再兜一次并发）
            where=OppIpDetail.status != "success",
        ))


# ---------------------------------------------------------------- 轮询入口


SessionFactory = Callable[[], AsyncSession] | async_sessionmaker


async def run_one(session_factory: SessionFactory = AsyncSessionFactory,
                  mcp_factory: Callable[[], McpSession] | None = None,
                  llm_factory: Callable[[], LlmClient] | None = None) -> bool:
    """认领并执行一个任务。返回 True 表示做了工作（轮询据此立即开始下一轮）。"""
    async with session_factory() as session:
        snap = await _claim(session)
    if snap is None:
        return False
    if snap is True:
        return True
    assert isinstance(snap, Snapshot)
    try:
        out = await _execute(snap, mcp_factory or make_mcp_session, llm_factory or make_llm_client)
    except Exception:
        # 客户端本身不抛供应商错误；走到这里是代码缺陷。任务留在 running，由租约回收为 unknown。
        logger.exception("IP 专家任务 %s 执行异常", snap.task_id)
        return True
    async with session_factory() as session:
        await _finalize(session, snap, out)
    return True


async def reap_expired(session_factory: SessionFactory = AsyncSessionFactory) -> int:
    """running 租约过期 → unknown，不重发。返回回收数。"""
    async with session_factory() as session:
        ids = (await session.execute(
            update(OppIpTask).where(OppIpTask.status == "running", OppIpTask.lease_until < _now())
            .values(status="unknown", error_category=INTERRUPTED, finished_at=_now(), lease_until=None)
            .returning(OppIpTask.id)
        )).scalars().all()
        await session.commit()
    for tid in ids:
        logger.warning("IP 专家任务 %s 租约过期，标记为结果未知（不自动重发）", tid)
    return len(ids)


async def run_one_ip_task_round() -> bool:
    try:
        return await run_one()
    except Exception:
        logger.exception("IP 专家任务轮询出错")
        return False


async def reap_ip_tasks_round() -> bool:
    """维护性任务：恒返回 False，不参与「整轮重来」判定（见 app/jobs/poller.py）。"""
    try:
        await reap_expired()
    except Exception:
        logger.exception("IP 专家任务租约回收出错")
    return False
