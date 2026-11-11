"""外观专利核查单与人工意见（change add-patent-risk-check）。

1. **私有性在每条查询里**：所有读写都带 ``owner_id = 当前用户``，取不到一律 404（超管也一样）。
2. **请求路径从不调用智慧芽**：创建、翻页、补充详情只写任务行（202），由 ``task_service`` 在
   后台认领执行。刷新、轮询、重绘只读库。
3. **幂等**：客户端令牌 + 输入指纹。同令牌同输入返回原结果，同令牌不同输入 409（决策 8）。

供以后机会中心 Agent 复用：本模块只依赖 ``AsyncSession`` 与 ``owner_id``，不依赖 HTTP 请求对象。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.site_registry import SITE_CATALOG
from app.table.risk.jurisdictions import (
    DEFAULT_PAGE_SIZE,
    JURISDICTIONS,
    MARKET_RECOMMENDATIONS,
    MAX_DETAIL_BATCH,
    MAX_IMAGE_BYTES,
    MAX_OFFSET,
    PAGE_SIZE_OPTIONS,
)
from app.table.risk.review_rules import validate_review
from app.table.risk.risk_errors import (
    CandidateNotFoundError,
    CheckNotFoundError,
    InvalidCheckInputError,
    InvalidImageError,
    NothingToFetchError,
    ProviderNotConfiguredError,
    TaskInProgressError,
    TokenConflictError,
)
from app.table.risk.risk_model import (
    ACTIVE_TASK_STATUSES,
    RiskCandidate,
    RiskCheck,
    RiskCheckImage,
    RiskPatentDetail,
    RiskProviderCall,
    RiskReview,
    RiskSearchPage,
    RiskTask,
)
from app.table.risk.risk_schemas import CheckCreate, EvidenceRequest, ReviewCreate
from app.user.user_model import UserCenter
from config import settings

#: 附图地址官方说明 9 小时过期；超过后界面提示可能失效。
IMAGE_URL_TTL = timedelta(hours=9)


def provider_configured() -> bool:
    return bool(settings.ZHIHUIYA_API_KEY)


def _now() -> datetime:
    return datetime.now(UTC)


def fingerprint(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


# ---------------------------------------------------------------- 元数据


def meta() -> dict[str, Any]:
    return {
        "provider": "zhihuiya",
        "provider_configured": provider_configured(),
        "markets": [{"code": s.code, "name": s.name_zh} for s in SITE_CATALOG.values()],
        "jurisdictions": [
            {"code": j.code, "name": j.name_zh, "note": j.note, "verified": j.verified}
            for j in JURISDICTIONS.values()
        ],
        "recommendations": {m: list(js) for m, js in MARKET_RECOMMENDATIONS.items()},
        "page_size_options": list(PAGE_SIZE_OPTIONS),
        "default_page_size": DEFAULT_PAGE_SIZE,
        "max_offset": MAX_OFFSET,
        "max_detail_batch": MAX_DETAIL_BATCH,
        "max_image_bytes": MAX_IMAGE_BYTES,
    }


# ---------------------------------------------------------------- 图片


def sniff_image(content: bytes) -> str:
    """按文件头判断实际格式，不信扩展名和浏览器 MIME。"""
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise InvalidImageError(log_detail=f"size={len(content)}")
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    raise InvalidImageError(log_detail="magic mismatch")


# ---------------------------------------------------------------- 核查单


async def _check(db: AsyncSession, owner_id: int, check_id: int, *, lock: bool = False) -> RiskCheck:
    stmt = select(RiskCheck).where(RiskCheck.id == check_id, RiskCheck.owner_id == owner_id)
    if lock:
        stmt = stmt.with_for_update()
    check = (await db.execute(stmt)).scalar_one_or_none()
    if check is None:
        raise CheckNotFoundError(log_detail=f"check={check_id} owner={owner_id}")
    return check


async def create_check(db: AsyncSession, owner_id: int, req: CheckCreate, image: bytes) -> dict[str, Any]:
    if not provider_configured():
        raise ProviderNotConfiguredError()
    bad_markets = [m for m in req.markets if m not in SITE_CATALOG]
    if bad_markets:
        raise InvalidCheckInputError(f"未知销售市场：{', '.join(bad_markets)}")
    bad_j = [j for j in req.jurisdictions if j not in JURISDICTIONS]
    if bad_j:
        raise InvalidCheckInputError(f"不支持的检索法域：{', '.join(bad_j)}")
    content_type = sniff_image(image)
    image_sha = hashlib.sha256(image).hexdigest()
    fp = fingerprint({**req.model_dump(exclude={"client_token"}), "image": image_sha})

    existing = await _check_by_token(db, owner_id, req.client_token)
    if existing is not None:
        return _same_or_conflict(existing, fp)
    if req.previous_check_id is not None:
        await _check(db, owner_id, req.previous_check_id)

    check = RiskCheck(
        owner_id=owner_id, product_name=req.product_name, product_version=req.product_version,
        source_ref=req.source_ref, description=req.description, markets=req.markets,
        jurisdictions=req.jurisdictions, page_size=req.page_size, previous_check_id=req.previous_check_id,
        client_token=req.client_token, input_fingerprint=fp, search_status="queued",
    )
    db.add(check)
    try:
        await db.flush()
        db.add(RiskCheckImage(check_id=check.id, content_type=content_type, size_bytes=len(image),
                              sha256=image_sha, data=image))
        db.add(RiskTask(check_id=check.id, kind="search", status="queued",
                        params={"offset": 0, "limit": req.page_size},
                        client_token=req.client_token, input_fingerprint=fp))
        await db.commit()
    except IntegrityError:
        # 双击并发：另一个请求先插入了同令牌的核查单
        await db.rollback()
        existing = await _check_by_token(db, owner_id, req.client_token)
        if existing is None:
            raise
        return _same_or_conflict(existing, fp)
    return {"id": check.id, "search_status": check.search_status}


async def _check_by_token(db: AsyncSession, owner_id: int, token: str) -> RiskCheck | None:
    return (await db.execute(select(RiskCheck).where(
        RiskCheck.owner_id == owner_id, RiskCheck.client_token == token))).scalar_one_or_none()


def _same_or_conflict(check: RiskCheck, fp: str) -> dict[str, Any]:
    if check.input_fingerprint != fp:
        raise TokenConflictError(log_detail=f"check={check.id}")
    return {"id": check.id, "search_status": check.search_status}


def _reviewed_markets_subq():
    return (
        select(func.count(func.distinct(RiskReview.market)))
        .where(RiskReview.check_id == RiskCheck.id)
        .scalar_subquery()
    )


async def list_checks(db: AsyncSession, owner_id: int, *, page: int, page_size: int,
                      search_status: str | None = None, review_state: str | None = None) -> dict[str, Any]:
    reviewed = _reviewed_markets_subq()
    market_count = func.jsonb_array_length(RiskCheck.markets)
    conds = [RiskCheck.owner_id == owner_id]
    if search_status:
        conds.append(RiskCheck.search_status == search_status)
    if review_state == "none":
        conds.append(reviewed == 0)
    elif review_state == "partial":
        conds.append(and_(reviewed > 0, reviewed < market_count))
    elif review_state == "complete":
        conds.append(reviewed >= market_count)

    total = (await db.execute(select(func.count()).select_from(RiskCheck).where(*conds))).scalar_one()
    rows = (await db.execute(
        select(RiskCheck, reviewed.label("reviewed"))
        .where(*conds)
        .order_by(RiskCheck.created_at.desc(), RiskCheck.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).all()
    return {
        "total": total, "page": page, "page_size": page_size,
        "items": [
            {
                **_check_summary(c),
                "reviewed_markets": int(n or 0),
            }
            for c, n in rows
        ],
    }


def _check_summary(c: RiskCheck) -> dict[str, Any]:
    return {
        "id": c.id, "product_name": c.product_name, "product_version": c.product_version,
        "markets": c.markets, "jurisdictions": c.jurisdictions, "search_status": c.search_status,
        "fetched_count": c.fetched_count, "total_count": c.total_count,
        "created_at": c.created_at, "searched_at": c.searched_at,
    }


async def get_check(db: AsyncSession, owner_id: int, check_id: int) -> dict[str, Any]:
    c = await _check(db, owner_id, check_id)
    tasks = (await db.execute(
        select(RiskTask).where(RiskTask.check_id == c.id).order_by(RiskTask.id.desc()).limit(50)
    )).scalars().all()
    pages = (await db.execute(
        select(RiskSearchPage.id, RiskSearchPage.offset, RiskSearchPage.limit, RiskSearchPage.returned_count,
               RiskSearchPage.total_count, RiskSearchPage.fetched_at, RiskSearchPage.task_id)
        .where(RiskSearchPage.check_id == c.id).order_by(RiskSearchPage.id)
    )).all()
    candidates = (await db.execute(
        select(RiskCandidate).where(RiskCandidate.check_id == c.id).order_by(RiskCandidate.rank, RiskCandidate.id)
    )).scalars().all()
    details = (await db.execute(
        select(RiskPatentDetail).join(RiskCandidate, RiskCandidate.id == RiskPatentDetail.candidate_id)
        .where(RiskCandidate.check_id == c.id)
    )).scalars().all()
    reviews = (await db.execute(
        select(RiskReview).where(RiskReview.check_id == c.id).order_by(RiskReview.id.desc())
    )).scalars().all()
    calls = (await db.execute(
        select(RiskProviderCall.api, RiskProviderCall.outcome, func.count())
        .join(RiskTask, RiskTask.id == RiskProviderCall.task_id)
        .where(RiskTask.check_id == c.id)
        .group_by(RiskProviderCall.api, RiskProviderCall.outcome)
    )).all()
    signer_names = await _user_names(db, {r.signer_id for r in reviews})
    next_ids = (await db.execute(
        select(RiskCheck.id).where(RiskCheck.previous_check_id == c.id, RiskCheck.owner_id == owner_id)
        .order_by(RiskCheck.id)
    )).scalars().all()
    previous_id = c.previous_check_id
    if previous_id is not None:
        # 上一轮被删或不属于本人时不暴露（previous 创建时已校验归属，这里再兜一次）
        owned = (await db.execute(select(exists().where(
            RiskCheck.id == previous_id, RiskCheck.owner_id == owner_id)))).scalar()
        previous_id = previous_id if owned else None

    by_candidate: dict[int, dict[str, Any]] = {}
    for d in details:
        by_candidate.setdefault(d.candidate_id, {})[d.kind] = {
            "status": d.status, "error_category": d.error_category, "fetched_at": d.fetched_at,
            "data": (d.data or {}).get("normalized") if d.status == "success" else None,
        }
    now = _now()
    active = {t.kind for t in tasks if t.status in ACTIVE_TASK_STATUSES}
    reviews_by_market: dict[str, list[dict[str, Any]]] = {m: [] for m in c.markets}
    for r in reviews:
        reviews_by_market.setdefault(r.market, []).append(_review_out(r, signer_names))

    return {
        **_check_summary(c),
        "source_ref": c.source_ref, "description": c.description, "page_size": c.page_size,
        "previous_check_id": previous_id, "next_check_ids": list(next_ids),
        "provider_configured": provider_configured(),
        "jurisdiction_details": [
            {"code": j, "name": JURISDICTIONS[j].name_zh if j in JURISDICTIONS else j,
             "note": JURISDICTIONS[j].note if j in JURISDICTIONS else "",
             "verified": JURISDICTIONS[j].verified if j in JURISDICTIONS else False}
            for j in c.jurisdictions
        ],
        "search_completed": c.searched_at is not None,
        "can_fetch_more": _can_fetch_more(c) and "search" not in active,
        "can_retry": c.search_status in ("failed", "unknown") and "search" not in active,
        "active_kinds": sorted(active),
        "tasks": [
            {"id": t.id, "kind": t.kind, "status": t.status, "error_category": t.error_category,
             "params": t.params, "created_at": t.created_at, "started_at": t.started_at,
             "finished_at": t.finished_at}
            for t in tasks
        ],
        "pages": [
            {"id": p.id, "offset": p.offset, "limit": p.limit, "returned_count": p.returned_count,
             "total_count": p.total_count, "fetched_at": p.fetched_at, "task_id": p.task_id}
            for p in pages
        ],
        "candidates": [
            {
                "id": k.id, "rank": k.rank, "patent_id": k.patent_id, "patent_pn": k.patent_pn,
                "title": k.title, "image_url": k.image_url, "score": k.score, "authority": k.authority,
                "apno": k.apno, "apdt": k.apdt, "pbdt": k.pbdt, "assignee": k.assignee, "loc": k.loc,
                "fetched_at": k.fetched_at, "image_maybe_expired": now - k.fetched_at > IMAGE_URL_TTL,
                "details": by_candidate.get(k.id, {}),
            }
            for k in candidates
        ],
        "reviews": reviews_by_market,
        "calls": [{"api": a, "outcome": o, "count": n} for a, o, n in calls],
    }


def _can_fetch_more(c: RiskCheck) -> bool:
    return (c.searched_at is not None and c.total_count is not None
            and c.fetched_count < c.total_count and c.fetched_count <= MAX_OFFSET)


async def _user_names(db: AsyncSession, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = (await db.execute(select(UserCenter.id, UserCenter.display_name, UserCenter.user_name)
                             .where(UserCenter.id.in_(ids)))).all()
    return {i: d or n for i, d, n in rows}


def _review_out(r: RiskReview, names: dict[int, str]) -> dict[str, Any]:
    return {
        "id": r.id, "market": r.market, "risk_level": r.risk_level, "decision": r.decision,
        "basis": r.basis, "uncovered_scope": r.uncovered_scope, "conditions": r.conditions,
        "evidence_candidate_ids": r.evidence_candidate_ids, "review_date": r.review_date,
        "signer_id": r.signer_id, "signer_name": names.get(r.signer_id), "created_at": r.created_at,
    }


async def get_image(db: AsyncSession, owner_id: int, check_id: int) -> tuple[bytes, str]:
    await _check(db, owner_id, check_id)
    img = await db.get(RiskCheckImage, check_id)
    if img is None:
        raise CheckNotFoundError(log_detail=f"image missing check={check_id}")
    return img.data, img.content_type


# ---------------------------------------------------------------- 显式付费动作


async def _existing_task(db: AsyncSession, check_id: int, token: str) -> RiskTask | None:
    return (await db.execute(select(RiskTask).where(
        RiskTask.check_id == check_id, RiskTask.client_token == token))).scalar_one_or_none()


async def _enqueue(db: AsyncSession, check: RiskCheck, *, kind: str, params: dict[str, Any],
                   token: str, fp: str) -> dict[str, Any]:
    task = RiskTask(check_id=check.id, kind=kind, status="queued", params=params,
                    client_token=token, input_fingerprint=fp)
    db.add(task)
    if kind == "search":
        check.search_status = "queued"
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await _existing_task(db, check.id, token)
        if existing is not None:
            return _task_same_or_conflict(existing, fp)
        raise TaskInProgressError(log_detail=f"check={check.id} kind={kind}") from None
    return {"task_id": task.id, "status": task.status}


def _task_same_or_conflict(task: RiskTask, fp: str) -> dict[str, Any]:
    if task.input_fingerprint != fp:
        raise TokenConflictError(log_detail=f"task={task.id}")
    return {"task_id": task.id, "status": task.status}


async def _guard(db: AsyncSession, check: RiskCheck, token: str, fp: str, kind: str) -> dict[str, Any] | None:
    existing = await _existing_task(db, check.id, token)
    if existing is not None:
        return _task_same_or_conflict(existing, fp)
    if not provider_configured():
        raise ProviderNotConfiguredError()
    busy = (await db.execute(select(exists().where(
        RiskTask.check_id == check.id, RiskTask.kind == kind,
        RiskTask.status.in_(ACTIVE_TASK_STATUSES))))).scalar()
    if busy:
        raise TaskInProgressError()
    return None


async def request_next_page(db: AsyncSession, owner_id: int, check_id: int, token: str) -> dict[str, Any]:
    check = await _check(db, owner_id, check_id, lock=True)
    fp = fingerprint({"kind": "page"})
    if (done := await _guard(db, check, token, fp, "search")) is not None:
        return done
    if not _can_fetch_more(check):
        raise NothingToFetchError()
    params = {"offset": check.fetched_count, "limit": check.page_size}
    return await _enqueue(db, check, kind="search", params=params, token=token, fp=fp)


async def request_retry(db: AsyncSession, owner_id: int, check_id: int, token: str) -> dict[str, Any]:
    """用户显式重新检索：新任务、新调用记录，不把重试包装成免费（决策 3）。"""
    check = await _check(db, owner_id, check_id, lock=True)
    fp = fingerprint({"kind": "retry"})
    if (done := await _guard(db, check, token, fp, "search")) is not None:
        return done
    if check.search_status not in ("failed", "unknown"):
        raise NothingToFetchError("当前检索没有失败或未知的结果，无需重新检索")
    last = (await db.execute(
        select(RiskTask).where(RiskTask.check_id == check.id, RiskTask.kind == "search")
        .order_by(RiskTask.id.desc()).limit(1)
    )).scalar_one()
    params = {"offset": int(last.params["offset"]), "limit": int(last.params["limit"]), "retry_of": last.id}
    return await _enqueue(db, check, kind="search", params=params, token=token, fp=fp)


async def request_details(db: AsyncSession, owner_id: int, check_id: int, req: EvidenceRequest) -> dict[str, Any]:
    check = await _check(db, owner_id, check_id, lock=True)
    fp = fingerprint({"kind": "detail", "ids": req.candidate_ids, "kinds": req.kinds})
    existing = await _existing_task(db, check.id, req.client_token)
    if existing is not None:
        return _task_same_or_conflict(existing, fp)
    owned = (await db.execute(select(func.count()).select_from(RiskCandidate).where(
        RiskCandidate.check_id == check.id, RiskCandidate.id.in_(req.candidate_ids)))).scalar_one()
    if owned != len(req.candidate_ids):
        raise CandidateNotFoundError(log_detail=f"check={check.id}")
    done = (await db.execute(select(func.count()).select_from(RiskPatentDetail).where(
        RiskPatentDetail.candidate_id.in_(req.candidate_ids), RiskPatentDetail.kind.in_(req.kinds),
        RiskPatentDetail.status == "success"))).scalar_one()
    if done == len(req.candidate_ids) * len(req.kinds):
        # 全部已有成功结果：直接复用，不产生调用
        return {"task_id": None, "status": "completed", "skipped": True}
    if (dup := await _guard(db, check, req.client_token, fp, "detail")) is not None:
        return dup
    params = {"candidate_ids": req.candidate_ids, "kinds": req.kinds}
    return await _enqueue(db, check, kind="detail", params=params, token=req.client_token, fp=fp)


# ---------------------------------------------------------------- 人工意见


async def add_review(db: AsyncSession, owner_id: int, check_id: int, req: ReviewCreate) -> dict[str, Any]:
    check = await _check(db, owner_id, check_id, lock=True)
    fp = fingerprint(req.model_dump(exclude={"client_token"}))
    existing = (await db.execute(select(RiskReview).where(
        RiskReview.check_id == check.id, RiskReview.client_token == req.client_token))).scalar_one_or_none()
    if existing is not None:
        if existing.input_fingerprint != fp:
            raise TokenConflictError(log_detail=f"review={existing.id}")
        return _review_out(existing, await _user_names(db, {existing.signer_id}))

    owned = len(req.evidence_candidate_ids)
    if req.evidence_candidate_ids and req.market in check.markets:
        owned = (await db.execute(select(func.count()).select_from(RiskCandidate).where(
            RiskCandidate.check_id == check.id, RiskCandidate.id.in_(req.evidence_candidate_ids)))).scalar_one()
    validate_review(req, markets=check.markets, evidence_owned=owned == len(req.evidence_candidate_ids),
                    search_done=check.searched_at is not None)

    review = RiskReview(
        check_id=check.id, market=req.market, risk_level=req.risk_level, decision=req.decision,
        basis=req.basis, uncovered_scope=req.uncovered_scope, conditions=req.conditions,
        evidence_candidate_ids=req.evidence_candidate_ids, review_date=req.review_date,
        signer_id=owner_id, client_token=req.client_token, input_fingerprint=fp,
    )
    db.add(review)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return await add_review(db, owner_id, check_id, req)
    return _review_out(review, await _user_names(db, {owner_id}))
