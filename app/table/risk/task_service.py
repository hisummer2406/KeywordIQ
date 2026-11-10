"""核查任务的认领、执行与收尾（change add-patent-risk-check 决策 3、4、6、7）。

三段式，**调用供应商期间不持有任何数据库会话**：

1. ``_claim``：短事务里 ``FOR UPDATE SKIP LOCKED`` 取一个 queued 任务，改 running 并写租约，
   把执行所需的一切（参数、临时图片地址、必要时的原图、待取详情的 patent_id）抄成快照，提交。
2. ``_execute``：只拿快照调用智慧芽，返回调用结果列表。
3. ``_finalize``：新事务写入调用留痕与证据，收尾任务状态。

生产 4 个 worker 都跑这个轮询，同一任务只会被一个 worker 认领。running 租约过期由
``reap_expired`` 改为 unknown，**不自动重发**——扣费可能已经发生。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.table.risk.jurisdictions import JURISDICTIONS
from app.table.risk.provider_client import CallResult, ZhihuiyaClient
from app.table.risk.risk_model import (
    RiskCandidate,
    RiskCheck,
    RiskCheckImage,
    RiskPatentDetail,
    RiskProviderCall,
    RiskSearchPage,
    RiskTask,
)
from config import settings
from database import AsyncSessionFactory

logger = logging.getLogger(__name__)

#: 临时上传地址离过期不足此值就重新上传（决策 6）。
UPLOAD_URL_MARGIN = timedelta(minutes=10)
#: P010 未返回 expire 时按官方说明的 9 小时计。
DEFAULT_UPLOAD_TTL_SECONDS = 32400

NOT_CONFIGURED = "not_configured"
INTERRUPTED = "interrupted"


def _now() -> datetime:
    return datetime.now(UTC)


def make_client() -> ZhihuiyaClient:
    """测试替换这个工厂注入 ``httpx.MockTransport``。"""
    return ZhihuiyaClient(settings.ZHIHUIYA_API_KEY, timeout=settings.RISK_PROVIDER_TIMEOUT_SECONDS)


@dataclass
class Snapshot:
    task_id: int
    kind: str
    check_id: int
    params: dict[str, Any]
    countries: list[str] = field(default_factory=list)
    upload_url: str | None = None
    image: tuple[bytes, str] | None = None
    #: detail：[(candidate_id, patent_id, 需取的种类)]
    targets: list[tuple[int, str, list[str]]] = field(default_factory=list)


@dataclass
class Outcome:
    calls: list[CallResult] = field(default_factory=list)


# ---------------------------------------------------------------- 认领


async def _claim(session: AsyncSession) -> Snapshot | bool | None:
    """返回快照；没有任务返回 None；认领后因未配置直接失败返回 True（做了工作，但无需调用）。"""
    task = (await session.execute(
        select(RiskTask).where(RiskTask.status == "queued")
        .order_by(RiskTask.created_at, RiskTask.id).limit(1)
        .with_for_update(skip_locked=True)
    )).scalar_one_or_none()
    if task is None:
        return None
    check = await session.get(RiskCheck, task.check_id)
    now = _now()

    if not settings.ZHIHUIYA_API_KEY:
        task.status, task.error_category = "failed", NOT_CONFIGURED
        task.started_at = task.finished_at = now
        if task.kind == "search":
            check.search_status = "failed"
        await session.commit()
        return True

    task.status, task.started_at = "running", now
    task.lease_until = now + timedelta(seconds=settings.RISK_TASK_LEASE_SECONDS)
    snap = Snapshot(task_id=task.id, kind=task.kind, check_id=check.id, params=dict(task.params))
    if task.kind == "search":
        check.search_status = "running"
        snap.countries = [JURISDICTIONS[j].vendor_code for j in check.jurisdictions if j in JURISDICTIONS]
        if check.upload_url and check.upload_expires_at and check.upload_expires_at - now > UPLOAD_URL_MARGIN:
            snap.upload_url = check.upload_url
        else:
            img = await session.get(RiskCheckImage, check.id)
            snap.image = (img.data, img.content_type)
    else:
        ids = [int(i) for i in task.params["candidate_ids"]]
        kinds = list(task.params["kinds"])
        cands = (await session.execute(
            select(RiskCandidate.id, RiskCandidate.patent_id)
            .where(RiskCandidate.check_id == check.id, RiskCandidate.id.in_(ids))
        )).all()
        done = set((await session.execute(
            select(RiskPatentDetail.candidate_id, RiskPatentDetail.kind)
            .where(RiskPatentDetail.candidate_id.in_(ids), RiskPatentDetail.status == "success")
        )).all())
        for cid, pid in cands:
            need = [k for k in kinds if (cid, k) not in done]
            if need:
                snap.targets.append((cid, pid, need))
    await session.commit()
    return snap


# ---------------------------------------------------------------- 执行（不持有会话）


async def _execute(snap: Snapshot, client: ZhihuiyaClient) -> Outcome:
    out = Outcome()
    if snap.kind == "search":
        url = snap.upload_url
        if url is None:
            data, ctype = snap.image  # type: ignore[misc]
            up = await client.upload_image(data, ctype)
            out.calls.append(up)
            if not up.ok:
                return out
            url = up.data["url"]
        res = await client.search_image(url=url, countries=snap.countries,
                                        limit=int(snap.params["limit"]), offset=int(snap.params["offset"]))
        out.calls.append(res)
        return out

    for kind, fn in (("bibliography", client.simple_bibliography), ("legal", client.legal_status)):
        ids = [pid for _, pid, need in snap.targets if kind in need]
        if ids:
            res = await fn(ids)
            res.extra["kind"] = kind
            out.calls.append(res)
    return out


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
    task = await session.get(RiskTask, snap.task_id, with_for_update=True)
    check = await session.get(RiskCheck, snap.check_id, with_for_update=True)
    for c in out.calls:
        session.add(RiskProviderCall(
            task_id=task.id, api=c.api, outcome=c.outcome, http_status=c.http_status,
            error_code=c.error_code, error_category=c.error_category,
            started_at=c.started_at, finished_at=c.finished_at,
        ))
    status, category = _task_status(out.calls)
    if not out.calls:
        status, category = "completed", None  # detail 任务全部已有成功结果

    if snap.kind == "search":
        for c in out.calls:
            if c.api == "P010" and c.ok:
                check.upload_url = c.data["url"]
                ttl = c.data.get("expire")
                ttl = ttl if isinstance(ttl, int) and ttl > 0 else DEFAULT_UPLOAD_TTL_SECONDS
                check.upload_expires_at = c.finished_at + timedelta(seconds=ttl)
        search = next((c for c in out.calls if c.api == "P060"), None)
        if status == "completed" and search is not None:
            await _save_page(session, snap, check, search)
        check.search_status = status
    else:
        await _save_details(session, snap, out)

    task.status, task.error_category = status, category
    task.finished_at, task.lease_until = _now(), None
    await session.commit()


async def _save_page(session: AsyncSession, snap: Snapshot, check: RiskCheck, res: CallResult) -> None:
    offset, limit = int(snap.params["offset"]), int(snap.params["limit"])
    items: list[dict[str, Any]] = res.data["patent_messages"]
    total = int(res.data["total_search_result_count"])
    page = RiskSearchPage(
        check_id=check.id, task_id=snap.task_id, offset=offset, limit=limit, returned_count=len(items),
        total_count=total, raw=res.data, fetched_at=res.finished_at,
        request_params={"patent_type": "D", "model": 1, "is_https": 1, "field": "SCORE",
                        "country": snap.countries, "limit": limit, "offset": offset},
    )
    session.add(page)
    await session.flush()
    rows = [
        {
            "check_id": check.id, "page_id": page.id, "rank": offset + i + 1,
            "patent_id": it["patent_id"], "patent_pn": _s(it.get("patent_pn")), "title": _s(it.get("title")),
            "image_url": _https(it.get("url")), "score": _f(it.get("score")),
            "authority": _s(it.get("authority")), "apno": _s(it.get("apno")),
            "apdt": _i(it.get("apdt")), "pbdt": _i(it.get("pbdt")),
            "assignee": _s(it.get("current_assignee")) or _s(it.get("original_assignee")),
            "loc": [str(x) for x in it["loc"]] if isinstance(it.get("loc"), list) else None,
            "fetched_at": res.finished_at,
        }
        for i, it in enumerate(items)
    ]
    if rows:
        await session.execute(pg_insert(RiskCandidate).values(rows).on_conflict_do_nothing(
            index_elements=["check_id", "patent_id"]))
    # 读取位置按请求的 limit 封顶：供应商多给的不算「已读取」到更后面的位置
    check.fetched_count = max(check.fetched_count, offset + min(len(items), limit))
    check.total_count = total
    check.searched_at = res.finished_at


async def _save_details(session: AsyncSession, snap: Snapshot, out: Outcome) -> None:
    for res in out.calls:
        kind = res.extra["kind"]
        by_pid = {it["patent_id"]: it for it in (res.data or [])} if res.ok else {}
        normalize = _norm_biblio if kind == "bibliography" else _norm_legal
        for cid, pid, need in snap.targets:
            if kind not in need:
                continue
            if res.ok:
                item = by_pid.get(pid)
                status = "success" if item is not None else "missing"
                data = {"normalized": normalize(item), "raw": item} if item is not None else None
                category = None
            else:
                status = "unknown" if res.outcome == "unknown" else "failed"
                data, category = None, res.error_category
            stmt = pg_insert(RiskPatentDetail).values(
                candidate_id=cid, kind=kind, status=status, task_id=snap.task_id,
                error_category=category, data=data, fetched_at=res.finished_at,
            )
            await session.execute(stmt.on_conflict_do_update(
                index_elements=["candidate_id", "kind"],
                set_={"status": stmt.excluded.status, "task_id": stmt.excluded.task_id,
                      "error_category": stmt.excluded.error_category, "data": stmt.excluded.data,
                      "fetched_at": stmt.excluded.fetched_at},
                # 已成功的不覆盖（认领时已排除，这里再兜一次并发）
                where=RiskPatentDetail.status != "success",
            ))


# ---------------------------------------------------------------- 规范化（供应商字段只做安全取值）


def _s(v: Any) -> str | None:
    return v.strip() or None if isinstance(v, str) else None


def _i(v: Any) -> int | None:
    try:
        return int(v) if v is not None and not isinstance(v, bool) else None
    except (TypeError, ValueError):
        return None


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None and not isinstance(v, bool) else None
    except (TypeError, ValueError):
        return None


def _https(v: Any) -> str | None:
    """只接受 https 地址；javascript:、http: 等一律丢弃。"""
    return v if isinstance(v, str) and v.startswith("https://") else None


def _texts(v: Any) -> list[str]:
    if not isinstance(v, list):
        return []
    return [t["text"] for t in v if isinstance(t, dict) and isinstance(t.get("text"), str)]


def _names(v: Any) -> list[str]:
    if not isinstance(v, list):
        return []
    return [p["name"] for p in v if isinstance(p, dict) and isinstance(p.get("name"), str)]


def _norm_biblio(item: dict[str, Any]) -> dict[str, Any]:
    bd = item.get("bibliographic_data") if isinstance(item.get("bibliographic_data"), dict) else {}
    parties = bd.get("parties") if isinstance(bd.get("parties"), dict) else (
        item.get("parties") if isinstance(item.get("parties"), dict) else {})
    app_ref = bd.get("application_reference") if isinstance(bd.get("application_reference"), dict) else {}
    pub_ref = bd.get("publication_reference") if isinstance(bd.get("publication_reference"), dict) else {}
    cls = bd.get("classification_data") if isinstance(bd.get("classification_data"), dict) else {}
    loc = cls.get("classification_loc")
    titles = _texts(bd.get("invention_title"))
    return {
        "pn": _s(item.get("pn")),
        "title": titles[0] if titles else None,
        "patent_type": _s(bd.get("patent_type")),
        "assignees": _names(parties.get("assignees")),
        "applicants": _names(parties.get("applicants")),
        "inventors": _names(parties.get("inventors")),
        "application_number": _s(app_ref.get("apno")) if app_ref else None,
        "application_date": _i(app_ref.get("date")) if app_ref else None,
        "publication_number": "".join(
            x for x in (_s(pub_ref.get("country")), _s(pub_ref.get("doc_number")), _s(pub_ref.get("kind"))) if x
        ) or None,
        "publication_date": _i(pub_ref.get("date")) if pub_ref else None,
        "loc": loc if isinstance(loc, list | dict) else None,
    }


def _norm_legal(item: dict[str, Any]) -> dict[str, Any]:
    legal = item.get("patent_legal") if isinstance(item.get("patent_legal"), dict) else {}

    def strs(v: Any) -> list[str]:
        return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []

    return {
        "pn": _s(item.get("pn")),
        "legal_date": _i(item.get("legal_date")),
        "simple_legal_status": strs(legal.get("simple_legal_status")),
        "legal_status": strs(legal.get("legal_status")),
        "event_status": strs(legal.get("event_status")),
    }


# ---------------------------------------------------------------- 轮询入口


SessionFactory = Callable[[], AsyncSession] | async_sessionmaker


async def run_one(session_factory: SessionFactory = AsyncSessionFactory,
                  client_factory: Callable[[], ZhihuiyaClient] | None = None) -> bool:
    """认领并执行一个任务。返回 True 表示做了工作（轮询据此立即开始下一轮）。"""
    async with session_factory() as session:
        snap = await _claim(session)
    if snap is None:
        return False
    if snap is True:
        return True
    assert isinstance(snap, Snapshot)
    client = (client_factory or make_client)()
    try:
        out = await _execute(snap, client)
    except Exception:
        # 客户端本身不抛供应商错误；走到这里是代码缺陷。任务留在 running，由租约回收为 unknown。
        logger.exception("核查任务 %s 执行异常", snap.task_id)
        return True
    async with session_factory() as session:
        await _finalize(session, snap, out)
    return True


async def reap_expired(session_factory: SessionFactory = AsyncSessionFactory) -> int:
    """running 租约过期 → unknown，不重发。返回回收数。"""
    async with session_factory() as session:
        rows = (await session.execute(
            update(RiskTask)
            .where(RiskTask.status == "running", RiskTask.lease_until < _now())
            .values(status="unknown", error_category=INTERRUPTED, finished_at=_now(), lease_until=None)
            .returning(RiskTask.id, RiskTask.check_id, RiskTask.kind)
        )).all()
        search_checks = [cid for _, cid, kind in rows if kind == "search"]
        if search_checks:
            await session.execute(
                update(RiskCheck).where(RiskCheck.id.in_(search_checks)).values(search_status="unknown"))
        await session.commit()
    for tid, _, _ in rows:
        logger.warning("核查任务 %s 租约过期，标记为结果未知（不自动重发）", tid)
    return len(rows)


async def run_one_risk_task_round() -> bool:
    try:
        return await run_one()
    except Exception:
        logger.exception("核查任务轮询出错")
        return False


async def reap_risk_tasks_round() -> bool:
    """维护性任务：恒返回 False，不参与「整轮重来」判定（见 app/jobs/poller.py）。"""
    try:
        await reap_expired()
    except Exception:
        logger.exception("核查任务租约回收出错")
    return False
