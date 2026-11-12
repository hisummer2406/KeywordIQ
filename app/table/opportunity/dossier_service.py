"""机会档案的业务层（change add-opportunity-dossier）。事务边界在这里，API 只是薄控制器。

- 团队可见：持有 ``opportunity:view`` 即可读写任意档案，所有写入署名（决策 2）。
- ABA 证据是只追加的确定性快照（决策 3，计算在 ``dossier_evidence``）。
- 人工证据只能作废；决定只追加并受规则约束（决策 4、5）。
- IP 调研只能关联自己名下的，档案向团队展示摘要（决策 6）。

请求路径不调用任何外部服务。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.site_registry import SITE_CATALOG, UnknownSiteError, normalize_site_code
from app.table.analysis.dimension_model import DimKeyword
from app.table.keyword.library_model import KeywordGroup, KeywordGroupItem
from app.table.opportunity import dossier_evidence as evidence
from app.table.opportunity import dossier_pricing
from app.table.opportunity.dossier_errors import (
    DecisionRuleError,
    DossierInputError,
    DossierNotFoundError,
    DossierTokenConflictError,
    EvidenceNotFoundError,
    KeywordGroupNotFoundError,
    KeywordLimitError,
    KeywordNotFoundError,
    LastKeywordError,
)
from app.table.opportunity.dossier_model import (
    ACTIVE_MESSAGE_STATUSES,
    GAP_CATEGORIES,
    MAX_KEYWORDS,
    RECHECK_REQUIRED,
    OppDossier,
    OppDossierDecision,
    OppDossierEvidence,
    OppDossierKeyword,
    OppDossierMessage,
)
from app.table.opportunity.dossier_schemas import (
    DecisionCreate,
    DossierCreate,
    DossierUpdate,
    ManualEvidenceCreate,
    normalize_keyword,
)
from app.table.opportunity.ip_errors import ResearchNotFoundError
from app.table.opportunity.ip_model import OppIpCandidate, OppIpResearch, OppIpReview, OppIpTask
from app.table.opportunity.llm_client import llm_configured
from app.table.opportunity.research_service import _user_names

_TZ = ZoneInfo("Asia/Shanghai")
#: 详情里带回的最近追问条数
MESSAGE_WINDOW = 50


def today() -> date:
    """业务上的「今天」（北京时间）：复查日期与待复查筛选都按它。"""
    return datetime.now(_TZ).date()


def _fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _resolve_site(site: str) -> str:
    try:
        return normalize_site_code(site)
    except UnknownSiteError as exc:
        # 非法站点必须报错，不回退默认站点（multi-site-data-model）
        raise DossierInputError(str(exc)) from exc


async def _dossier(db: AsyncSession, dossier_id: int, *, lock: bool = False) -> OppDossier:
    stmt = select(OppDossier).where(OppDossier.id == dossier_id)
    d = (await db.execute(stmt.with_for_update() if lock else stmt)).scalar_one_or_none()
    if d is None:
        raise DossierNotFoundError(log_detail=f"dossier={dossier_id}")
    return d


async def _touch(db: AsyncSession, dossier_id: int) -> None:
    await db.execute(update(OppDossier).where(OppDossier.id == dossier_id).values(updated_at=func.now()))


def _like_escape(text_: str) -> str:
    return text_.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _resolve_keywords(db: AsyncSession, site: str, keywords: list[str]) -> dict[str, int]:
    """按站点把**已规范化**的关键词解析到 ``dim_keyword``。解析不到的词不在返回值里。

    导入按报表原文入库（``importer._upsert_keywords`` 不改写），而档案里的词是规范化过的
    （小写、单空格）。绝大多数词两者一致，先走 ``(site, keyword)`` 唯一索引精确匹配；剩下的再用
    三元组索引找「规范化后相同」的原文（生产抽样约万分之 0.6 的词带多余空白）。不这样做，这些词
    会被标成「未进入过榜单」，而同一个词从词库分组带入（按 keyword_id）却能解析（PR #187 评审）。
    只读取、不改写 ``dim_keyword`` 的原文。
    """
    if not keywords:
        return {}
    rows = (await db.execute(select(DimKeyword.keyword, DimKeyword.keyword_id).where(
        DimKeyword.site == site, DimKeyword.keyword.in_(keywords)))).all()
    found = {kw: kid for kw, kid in rows}
    for kw in keywords:
        if kw in found:
            continue
        # 词与词之间允许任意空白、首尾允许空白；候选再按同一规范化函数精确比对
        pattern = "%" + "%".join(_like_escape(part) for part in kw.split(" ")) + "%"
        candidates = (await db.execute(
            select(DimKeyword.keyword, DimKeyword.keyword_id)
            .where(DimKeyword.site == site, DimKeyword.keyword.ilike(pattern, escape="\\"))
            .order_by(DimKeyword.keyword_id).limit(200))).all()
        match = next((kid for text_, kid in candidates if normalize_keyword(text_) == kw), None)
        if match is not None:
            found[kw] = match
    return found


async def _group_keywords(db: AsyncSession, user_id: int, group_id: int) -> tuple[str, list[tuple[str, int]]]:
    """当前用户词库分组的站点与条目。分组不属于当前用户时 404。"""
    group = (await db.execute(select(KeywordGroup).where(
        KeywordGroup.group_id == group_id, KeywordGroup.user_id == user_id))).scalar_one_or_none()
    if group is None:
        raise KeywordGroupNotFoundError(log_detail=f"group={group_id} user={user_id}")
    rows = (await db.execute(
        select(DimKeyword.keyword, DimKeyword.keyword_id)
        .join(KeywordGroupItem, KeywordGroupItem.keyword_id == DimKeyword.keyword_id)
        .where(KeywordGroupItem.group_id == group_id)
        .order_by(KeywordGroupItem.added_at, DimKeyword.keyword_id)
    )).all()
    return group.site, [(kw, kid) for kw, kid in rows]


# ---------------------------------------------------------------- 建档、列表、详情


async def create_dossier(db: AsyncSession, user_id: int, req: DossierCreate) -> dict[str, Any]:
    fp = _fingerprint(req.model_dump(exclude={"client_token"}))
    existing = (await db.execute(select(OppDossier).where(
        OppDossier.creator_id == user_id, OppDossier.client_token == req.client_token))).scalar_one_or_none()
    if existing is not None:
        return {"id": existing.id}

    resolved: dict[str, int | None] = {}
    if req.keyword_group_id is not None:
        group_site, items = await _group_keywords(db, user_id, req.keyword_group_id)
        site = group_site
        if req.site and _resolve_site(req.site) != site:
            raise DossierInputError(f"所选词库分组属于 {site} 站点，与请求的站点不一致")
        for kw, kid in items:
            resolved.setdefault(normalize_keyword(kw), kid)
    else:
        site = _resolve_site(req.site or "")

    extra = [kw for kw in req.keywords if kw not in resolved]
    found = await _resolve_keywords(db, site, extra)
    for kw in extra:
        resolved[kw] = found.get(kw)

    if not resolved:
        raise DossierInputError("所选词库分组里没有关键词")
    if len(resolved) > MAX_KEYWORDS:
        raise KeywordLimitError(f"一个档案最多 {MAX_KEYWORDS} 个关键词，当前 {len(resolved)} 个，请精简后再建档")

    d = OppDossier(title=req.title, idea=req.idea, site=site, creator_id=user_id, assignee_id=user_id,
                   client_token=req.client_token)
    db.add(d)
    try:
        await db.flush()
    except IntegrityError:
        # 并发的同令牌提交：另一个请求已建好
        await db.rollback()
        existing = (await db.execute(select(OppDossier).where(
            OppDossier.creator_id == user_id, OppDossier.client_token == req.client_token))).scalar_one_or_none()
        if existing is None:
            raise DossierTokenConflictError(log_detail=f"creator={user_id} fp={fp[:8]}") from None
        return {"id": existing.id}
    db.add_all([OppDossierKeyword(dossier_id=d.id, keyword=kw, keyword_id=kid, added_by=user_id)
                for kw, kid in resolved.items()])
    await db.flush()
    await _append_snapshot(db, d, user_id)
    await db.commit()
    return {"id": d.id}


async def list_dossiers(db: AsyncSession, user_id: int, *, page: int, page_size: int, status: str | None = None,
                        due: bool = False, mine: bool = False, archived: bool = False,
                        q: str | None = None, site: str | None = None) -> dict[str, Any]:
    conds = [OppDossier.archived_at.is_not(None) if archived else OppDossier.archived_at.is_(None)]
    if site is not None:
        # 在分页之前按站点筛：线索「并入已有档案」只能选同站点的档案（PR #188 评审 P2）
        conds.append(OppDossier.site == _resolve_site(site))
    if status:
        conds.append(OppDossier.status == status)
    if due:
        conds.append(OppDossier.recheck_date <= today())
    if mine:
        conds.append(OppDossier.assignee_id == user_id)
    if q and q.strip():
        like = f"%{_like_escape(q.strip())}%"
        kw_match = (select(OppDossierKeyword.id).where(OppDossierKeyword.dossier_id == OppDossier.id,
                                                       OppDossierKeyword.keyword.ilike(like, escape="\\"))
                    .correlate(OppDossier).exists())
        conds.append(OppDossier.title.ilike(like, escape="\\") | kw_match)

    total = (await db.execute(select(func.count()).select_from(OppDossier).where(*conds))).scalar_one()
    kw_count = (select(func.count()).where(OppDossierKeyword.dossier_id == OppDossier.id)
                .correlate(OppDossier).scalar_subquery())
    rows = (await db.execute(
        select(OppDossier, kw_count)
        .where(*conds)
        # updated_at 精度有限，同一时刻多条是常态：带上 id 作完整次级键，翻页不重不漏
        .order_by(OppDossier.updated_at.desc(), OppDossier.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).all()
    names = await _user_names(db, {d.assignee_id for d, _ in rows} | {d.creator_id for d, _ in rows})
    due_count = (await db.execute(select(func.count()).select_from(OppDossier).where(
        OppDossier.archived_at.is_(None), OppDossier.recheck_date <= today()))).scalar_one()
    return {
        "total": total, "page": page, "page_size": page_size, "due_count": due_count, "today": today(),
        "items": [_dossier_out(d, names) | {"keyword_count": int(n or 0)} for d, n in rows],
    }


def _dossier_out(d: OppDossier, names: dict[int, str]) -> dict[str, Any]:
    site = SITE_CATALOG.get(d.site)
    return {
        "id": d.id, "title": d.title, "idea": d.idea, "site": d.site, "status": d.status,
        # 站点中文名与 Amazon 域名来自站点注册表，前端不另存一份映射
        "site_name": site.name_zh if site else d.site, "site_domain": site.domain if site else None,
        "recheck_date": d.recheck_date, "archived_at": d.archived_at,
        "creator_id": d.creator_id, "creator_name": names.get(d.creator_id),
        "assignee_id": d.assignee_id, "assignee_name": names.get(d.assignee_id),
        "created_at": d.created_at, "updated_at": d.updated_at,
    }


def _snapshot_out(e: OppDossierEvidence, names: dict[int, str], *, payload: bool = True) -> dict[str, Any]:
    out = {"id": e.id, "anchor_day": e.anchor_day, "anchor_week": e.anchor_week,
           "created_by": e.created_by, "created_by_name": names.get(e.created_by), "created_at": e.created_at}
    if payload:
        out["payload"] = e.payload
    return out


def _manual_out(e: OppDossierEvidence, names: dict[int, str]) -> dict[str, Any]:
    return {
        "id": e.id, "category": e.category, "content": e.content, "source_name": e.source_name,
        "source_url": e.source_url, "observed_date": e.observed_date,
        "created_by": e.created_by, "created_by_name": names.get(e.created_by), "created_at": e.created_at,
        "voided_at": e.voided_at, "voided_by_name": names.get(e.voided_by) if e.voided_by else None,
    }


def _decision_out(x: OppDossierDecision, names: dict[int, str]) -> dict[str, Any]:
    return {
        "id": x.id, "verdict": x.verdict, "reason": x.reason, "next_step": x.next_step,
        "recheck_date": x.recheck_date, "snapshot_id": x.snapshot_id, "ip_status": x.ip_status,
        "pricing_status": x.pricing_status,
        "signer_id": x.signer_id, "signer_name": names.get(x.signer_id), "created_at": x.created_at,
    }


def message_out(m: OppDossierMessage, names: dict[int, str]) -> dict[str, Any]:
    return {
        "id": m.id, "question": m.question, "status": m.status, "error_category": m.error_category,
        "answer": m.answer, "cited_evidence_ids": m.cited_evidence_ids, "model": m.model,
        "asker_id": m.asker_id, "asker_name": names.get(m.asker_id),
        "created_at": m.created_at, "finished_at": m.finished_at,
    }


async def _snapshots(db: AsyncSession, dossier_id: int, limit: int = 2) -> list[OppDossierEvidence]:
    return list((await db.execute(
        select(OppDossierEvidence)
        .where(OppDossierEvidence.dossier_id == dossier_id, OppDossierEvidence.kind == "aba_snapshot")
        .order_by(OppDossierEvidence.id.desc()).limit(limit))).scalars().all())


async def _keyword_rows(db: AsyncSession, dossier_id: int) -> list[OppDossierKeyword]:
    return list((await db.execute(select(OppDossierKeyword).where(OppDossierKeyword.dossier_id == dossier_id)
                                  .order_by(OppDossierKeyword.id))).scalars().all())


async def ip_summary(db: AsyncSession, dossier_id: int) -> dict[str, Any]:
    """关联专利调研的摘要（决策 6）：只有这些字段向团队公开，明细仍按创建人私有。

    没有关联调研、或关联的调研都没有人工意见时是「未核查」——不是低风险。
    """
    researches = list((await db.execute(select(OppIpResearch).where(OppIpResearch.dossier_id == dossier_id)
                                        .order_by(OppIpResearch.id))).scalars().all())
    if not researches:
        return {"state": "unchecked", "researches": []}
    ids = [r.id for r in researches]
    cand = dict((await db.execute(select(OppIpCandidate.research_id, func.count())
                                  .where(OppIpCandidate.research_id.in_(ids))
                                  .group_by(OppIpCandidate.research_id))).all())
    last_task: dict[int, dict[str, Any]] = {}
    for rid, kind, status in (await db.execute(
            select(OppIpTask.research_id, OppIpTask.kind, OppIpTask.status)
            .where(OppIpTask.research_id.in_(ids)).order_by(OppIpTask.id))).all():
        last_task[rid] = {"kind": kind, "status": status}
    # 每个 (调研, 市场) 的最新一条人工意见
    latest_review: dict[int, dict[str, dict[str, Any]]] = {}
    for rv in (await db.execute(select(OppIpReview).where(OppIpReview.research_id.in_(ids))
                                .order_by(OppIpReview.id))).scalars().all():
        latest_review.setdefault(rv.research_id, {})[rv.market] = {
            "market": rv.market, "risk_level": rv.risk_level, "decision": rv.decision,
            "signer_id": rv.signer_id, "created_at": rv.created_at.isoformat(),
        }
    names = await _user_names(db, {r.owner_id for r in researches}
                              | {m["signer_id"] for by in latest_review.values() for m in by.values()})
    out = []
    for r in researches:
        reviews = [m | {"signer_name": names.get(m["signer_id"])} for m in latest_review.get(r.id, {}).values()]
        out.append({
            "id": r.id, "product_name": r.product_name, "owner_id": r.owner_id, "owner_name": names.get(r.owner_id),
            "markets": r.markets, "candidate_count": int(cand.get(r.id, 0)), "last_task": last_task.get(r.id),
            "reviews": sorted(reviews, key=lambda m: m["market"]), "created_at": r.created_at,
        })
    reviewed = any(x["reviews"] for x in out)
    return {"state": "reviewed" if reviewed else "unchecked", "researches": out}


def _ip_status_for_decision(summary: dict[str, Any]) -> dict[str, Any]:
    """签署决定时固化的专利状态：只留判断所需的字段。"""
    return {
        "state": summary["state"],
        "research_count": len(summary["researches"]),
        "reviews": [{"research_id": r["id"], "market": m["market"], "risk_level": m["risk_level"],
                     "decision": m["decision"]} for r in summary["researches"] for m in r["reviews"]],
    }


async def get_dossier(db: AsyncSession, user_id: int, dossier_id: int) -> dict[str, Any]:
    d = await _dossier(db, dossier_id)
    keywords = await _keyword_rows(db, d.id)
    snaps = await _snapshots(db, d.id)
    snapshot_count = (await db.execute(select(func.count()).select_from(OppDossierEvidence).where(
        OppDossierEvidence.dossier_id == d.id, OppDossierEvidence.kind == "aba_snapshot"))).scalar_one()
    manual = list((await db.execute(
        select(OppDossierEvidence)
        .where(OppDossierEvidence.dossier_id == d.id, OppDossierEvidence.kind == "manual")
        .order_by(OppDossierEvidence.id.desc()))).scalars().all())
    decisions = list((await db.execute(select(OppDossierDecision).where(OppDossierDecision.dossier_id == d.id)
                                       .order_by(OppDossierDecision.id.desc()))).scalars().all())
    messages = list((await db.execute(select(OppDossierMessage).where(OppDossierMessage.dossier_id == d.id)
                                      .order_by(OppDossierMessage.id.desc()).limit(MESSAGE_WINDOW))).scalars().all())
    messages.reverse()
    ip = await ip_summary(db, d.id)
    pricing = await dossier_pricing.pricing_summary(db, d.id)

    latest = snaps[0] if snaps else None
    base, basis = await _baseline(db, d.id, latest, snaps, decisions)
    changes = None
    if latest is not None and base is not None and base.id != latest.id:
        changes = evidence.compare(base.payload, latest.payload) | {
            "basis": basis, "base_snapshot_id": base.id, "base_created_at": base.created_at,
        }

    anchors = await evidence.latest_anchors(db, d.site)
    current_set = evidence.keyword_set([(k.keyword, k.keyword_id) for k in keywords])
    stale = None
    if latest is not None:
        stale = {
            "new_data": (anchors[0], anchors[1]) != (latest.anchor_day, latest.anchor_week),
            "keywords_changed": latest.payload.get("keyword_set") != current_set,
        }

    people = {d.creator_id, d.assignee_id} | {e.created_by for e in manual} | {e.voided_by for e in manual if e.voided_by}
    people |= {x.signer_id for x in decisions} | {m.asker_id for m in messages} | {s.created_by for s in snaps}
    names = await _user_names(db, people)
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for e in manual:
        by_cat.setdefault(e.category or "other", []).append(_manual_out(e, names))
    return {
        "dossier": _dossier_out(d, names),
        "today": today(),
        "keywords": [{"id": k.id, "keyword": k.keyword, "keyword_id": k.keyword_id,
                      "resolved": k.keyword_id is not None} for k in keywords],
        "snapshot": _snapshot_out(latest, names) if latest else None,
        "snapshot_count": snapshot_count,
        "snapshot_stale": stale,
        "latest_anchor_day": anchors[0], "latest_anchor_week": anchors[1],
        "changes": changes,
        # 固定缺口清单：恒为「未接入」，有人工证据也不改变这一事实
        "gaps": [{"category": c, "connected": False, "evidence": by_cat.get(c, [])} for c in GAP_CATEGORIES],
        "other_evidence": by_cat.get("other", []),
        "decisions": [_decision_out(x, names) for x in decisions],
        "ip": ip,
        "pricing": pricing,
        "messages": [message_out(m, names) for m in messages],
        "asking": any(m.status in ACTIVE_MESSAGE_STATUSES for m in messages),
        "llm_configured": llm_configured(),
    }


async def _baseline(db: AsyncSession, dossier_id: int, latest: OppDossierEvidence | None,
                    snaps: list[OppDossierEvidence],
                    decisions: list[OppDossierDecision]) -> tuple[OppDossierEvidence | None, str | None]:
    """变化的对比基线：最新决定所依据的快照；没有则是上一份快照。"""
    if latest is None:
        return None, None
    if decisions and decisions[0].snapshot_id is not None:
        if decisions[0].snapshot_id == latest.id:
            return None, None
        base = (await db.execute(select(OppDossierEvidence).where(
            OppDossierEvidence.id == decisions[0].snapshot_id,
            OppDossierEvidence.dossier_id == dossier_id))).scalar_one_or_none()
        return base, "decision"
    return (snaps[1], "previous") if len(snaps) > 1 else (None, None)


# ---------------------------------------------------------------- 档案信息、关键词、快照


async def update_dossier(db: AsyncSession, user_id: int, dossier_id: int, req: DossierUpdate) -> dict[str, Any]:
    d = await _dossier(db, dossier_id, lock=True)
    if req.title is not None:
        d.title = req.title
    if req.idea is not None:
        d.idea = req.idea or None
    if req.take_over:
        d.assignee_id = user_id
    if req.archived is not None:
        d.archived_at = (d.archived_at or datetime.now(_TZ)) if req.archived else None
    d.updated_at = func.now()
    await db.commit()
    return {"id": d.id}


async def add_keywords(db: AsyncSession, user_id: int, dossier_id: int, keywords: list[str]) -> dict[str, Any]:
    d = await _dossier(db, dossier_id, lock=True)
    existing = {k.keyword for k in await _keyword_rows(db, d.id)}
    new = [kw for kw in keywords if kw not in existing]
    if len(existing) + len(new) > MAX_KEYWORDS:
        raise KeywordLimitError(f"一个档案最多 {MAX_KEYWORDS} 个关键词，已有 {len(existing)} 个")
    found = await _resolve_keywords(db, d.site, new)
    db.add_all([OppDossierKeyword(dossier_id=d.id, keyword=kw, keyword_id=found.get(kw), added_by=user_id)
                for kw in new])
    await db.flush()
    if new:
        await _append_snapshot(db, d, user_id)
    d.updated_at = func.now()
    await db.commit()
    return {"added": len(new), "unresolved": [kw for kw in new if kw not in found]}


async def remove_keyword(db: AsyncSession, user_id: int, dossier_id: int, keyword_row_id: int) -> None:
    d = await _dossier(db, dossier_id, lock=True)
    rows = await _keyword_rows(db, d.id)
    target = next((k for k in rows if k.id == keyword_row_id), None)
    if target is None:
        raise KeywordNotFoundError(log_detail=f"dossier={d.id} keyword_row={keyword_row_id}")
    if len(rows) == 1:
        raise LastKeywordError()
    await db.delete(target)
    await db.flush()
    await _append_snapshot(db, d, user_id)
    d.updated_at = func.now()
    await db.commit()


async def _append_snapshot(db: AsyncSession, d: OppDossier, user_id: int) -> tuple[OppDossierEvidence, bool]:
    """生成并追加一份快照；数据期次与关键词集合都没变时返回已有的那份（不追加）。"""
    keywords = [(k.keyword, k.keyword_id) for k in await _keyword_rows(db, d.id)]
    # 之前解析不到的词可能在新导入后进了榜：每次快照前重新解析一遍
    unresolved = [kw for kw, kid in keywords if kid is None]
    if unresolved:
        found = await _resolve_keywords(db, d.site, unresolved)
        if found:
            for kw, kid in found.items():
                await db.execute(update(OppDossierKeyword).where(
                    OppDossierKeyword.dossier_id == d.id, OppDossierKeyword.keyword == kw).values(keyword_id=kid))
            keywords = [(kw, found.get(kw, kid)) for kw, kid in keywords]

    anchors = await evidence.latest_anchors(db, d.site)
    latest = (await _snapshots(db, d.id, limit=1) or [None])[0]
    if (latest is not None and (latest.anchor_day, latest.anchor_week) == anchors
            and latest.payload.get("keyword_set") == evidence.keyword_set(keywords)):
        return latest, False
    payload = await evidence.build_snapshot(db, d.site, keywords, anchors)
    snap = OppDossierEvidence(dossier_id=d.id, kind="aba_snapshot", anchor_day=anchors[0], anchor_week=anchors[1],
                              payload=payload, created_by=user_id)
    db.add(snap)
    await db.flush()
    return snap, True


async def refresh_snapshot(db: AsyncSession, user_id: int, dossier_id: int) -> dict[str, Any]:
    d = await _dossier(db, dossier_id, lock=True)
    snap, created = await _append_snapshot(db, d, user_id)
    if created:
        d.updated_at = func.now()
    await db.commit()
    return {"snapshot_id": snap.id, "created": created}


# ---------------------------------------------------------------- 人工证据


async def add_manual_evidence(db: AsyncSession, user_id: int, dossier_id: int,
                              req: ManualEvidenceCreate) -> dict[str, Any]:
    d = await _dossier(db, dossier_id)
    if req.observed_date > today():
        raise DossierInputError("观测日期不能晚于今天")
    e = OppDossierEvidence(dossier_id=d.id, kind="manual", category=req.category, content=req.content,
                           source_name=req.source_name, source_url=req.source_url,
                           observed_date=req.observed_date, created_by=user_id)
    db.add(e)
    await _touch(db, d.id)
    await db.commit()
    return {"id": e.id}


async def void_manual_evidence(db: AsyncSession, user_id: int, dossier_id: int, evidence_id: int) -> dict[str, Any]:
    await _dossier(db, dossier_id)
    e = (await db.execute(select(OppDossierEvidence).where(
        OppDossierEvidence.id == evidence_id, OppDossierEvidence.dossier_id == dossier_id,
        OppDossierEvidence.kind == "manual").with_for_update())).scalar_one_or_none()
    if e is None:
        raise EvidenceNotFoundError(log_detail=f"dossier={dossier_id} evidence={evidence_id}")
    if e.voided_at is None:
        e.voided_at, e.voided_by = datetime.now(_TZ), user_id
        await _touch(db, dossier_id)
    await db.commit()
    return {"id": e.id}


# ---------------------------------------------------------------- 决定


async def add_decision(db: AsyncSession, user_id: int, dossier_id: int, req: DecisionCreate) -> dict[str, Any]:
    d = await _dossier(db, dossier_id, lock=True)
    fp_new = _fingerprint(req.model_dump(exclude={"client_token"}))
    dup = (await db.execute(select(OppDossierDecision).where(
        OppDossierDecision.dossier_id == d.id, OppDossierDecision.client_token == req.client_token))).scalar_one_or_none()
    if dup is not None:
        same = _fingerprint({"verdict": dup.verdict, "reason": dup.reason, "next_step": dup.next_step,
                             "recheck_date": dup.recheck_date}) == fp_new
        if not same:
            raise DossierTokenConflictError(log_detail=f"dossier={d.id} decision={dup.id}")
        return {"id": dup.id}

    if req.verdict in RECHECK_REQUIRED and req.recheck_date is None:
        raise DecisionRuleError("补证与观察必须定一个复查日期")
    if req.recheck_date is not None and req.recheck_date < today():
        raise DecisionRuleError("复查日期不能早于今天")

    latest = (await _snapshots(db, d.id, limit=1) or [None])[0]
    if req.verdict == "advance":
        # 日榜或周榜任一有在榜记录都算 ABA 证据：只上过周榜的词同样在快照里（PR #187 评审）
        has_aba = latest is not None and any(
            k.get("resolved") and (k["day"]["ranked_periods"] > 0 or k["week"]["ranked_periods"] > 0)
            for k in latest.payload["keywords"])
        has_manual = (await db.execute(select(func.count()).select_from(OppDossierEvidence).where(
            OppDossierEvidence.dossier_id == d.id, OppDossierEvidence.kind == "manual",
            OppDossierEvidence.voided_at.is_(None)))).scalar_one() > 0
        if not (has_aba or has_manual):
            raise DecisionRuleError("档案里还没有任何证据（关键词都没有在榜记录，也没有人工证据），不能签「推进」；请先补充证据")

    summary = await ip_summary(db, d.id)
    # 测算状态只记录、不阻塞（design 决策 4）；之后核价单再保存新版本，这条决定里的值不变
    pricing = dossier_pricing.status_for_decision(await dossier_pricing.pricing_summary(db, d.id))
    x = OppDossierDecision(dossier_id=d.id, verdict=req.verdict, reason=req.reason, next_step=req.next_step,
                           recheck_date=req.recheck_date, snapshot_id=latest.id if latest else None,
                           ip_status=_ip_status_for_decision(summary), pricing_status=pricing,
                           signer_id=user_id, client_token=req.client_token)
    db.add(x)
    # 状态与复查日期是最新决定的冗余，同一事务写入
    d.status, d.recheck_date, d.updated_at = req.verdict, req.recheck_date, func.now()
    await db.commit()
    return {"id": x.id}


# ---------------------------------------------------------------- 关联 IP 调研


async def _own_research(db: AsyncSession, user_id: int, research_id: int) -> OppIpResearch:
    r = (await db.execute(select(OppIpResearch).where(
        OppIpResearch.id == research_id, OppIpResearch.owner_id == user_id).with_for_update())).scalar_one_or_none()
    if r is None:
        # 不存在与不属于当前用户不区分（沿用调研的私有性）
        raise ResearchNotFoundError(log_detail=f"research={research_id} owner={user_id}")
    return r


async def link_research(db: AsyncSession, user_id: int, dossier_id: int, research_id: int) -> dict[str, Any]:
    d = await _dossier(db, dossier_id)
    r = await _own_research(db, user_id, research_id)
    r.dossier_id = d.id
    await _touch(db, d.id)
    await db.commit()
    return {"research_id": r.id, "dossier_id": d.id}


async def unlink_research(db: AsyncSession, user_id: int, dossier_id: int, research_id: int) -> None:
    await _dossier(db, dossier_id)
    r = await _own_research(db, user_id, research_id)
    if r.dossier_id == dossier_id:
        r.dossier_id = None
        await _touch(db, dossier_id)
    await db.commit()


async def my_linkable_researches(db: AsyncSession, user_id: int) -> dict[str, Any]:
    """当前用户名下可挂到档案的调研（最近 100 条），带各自当前关联的档案。"""
    rows = (await db.execute(
        select(OppIpResearch.id, OppIpResearch.product_name, OppIpResearch.dossier_id, OppIpResearch.created_at)
        .where(OppIpResearch.owner_id == user_id)
        .order_by(OppIpResearch.created_at.desc(), OppIpResearch.id.desc()).limit(100))).all()
    return {"items": [{"id": i, "product_name": n, "dossier_id": did, "created_at": c} for i, n, did, c in rows]}


# ---------------------------------------------------------------- 关联核价单（change add-opportunity-feasibility）


async def link_pricing_sheet(db: AsyncSession, user_id: int, dossier_id: int, sheet_id: int) -> dict[str, Any]:
    d = await _dossier(db, dossier_id)
    await dossier_pricing.link(db, user_id, d.id, sheet_id)
    await _touch(db, d.id)
    await db.commit()
    return {"sheet_id": sheet_id, "dossier_id": d.id}


async def unlink_pricing_sheet(db: AsyncSession, user_id: int, dossier_id: int, sheet_id: int) -> None:
    await _dossier(db, dossier_id)
    if await dossier_pricing.unlink(db, user_id, dossier_id, sheet_id):
        await _touch(db, dossier_id)
    await db.commit()


async def create_pricing_sheet(db: AsyncSession, user_id: int, dossier_id: int) -> dict[str, Any]:
    """从档案发起核价：草稿核价单（产品名 = 档案标题，市场 = 档案站点）并关联。"""
    d = await _dossier(db, dossier_id)
    sheet = await dossier_pricing.create_draft(db, user_id, d.id, d.title, d.site)
    await _touch(db, d.id)
    await db.commit()
    return {"sheet_id": sheet.id}


async def my_linkable_pricing_sheets(db: AsyncSession, user_id: int) -> dict[str, Any]:
    return await dossier_pricing.my_linkable(db, user_id)
