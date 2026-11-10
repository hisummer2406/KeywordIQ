"""共享费用规则：草稿维护、启用互斥、停用，以及按核价日期解析（design D8）。

启用的互斥在事务内对同一 ``(kind, scope_key)`` 的启用行 ``FOR UPDATE`` 后判断——两个人
同时启用重叠版本时，后到的一方会等前者提交再看到它，然后被拒，而不是两条都生效。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.site_registry import SITE_CATALOG
from app.table.pricing import rule_schemas as rs
from app.table.pricing.engine import types as t
from app.table.pricing.pricing_errors import (
    RuleConflictError,
    RuleImmutableError,
    RuleInvalidError,
    RuleNotFoundError,
    RuleOverlapError,
)
from app.table.pricing.pricing_model import PricingRule
from app.table.pricing.pricing_schemas import (
    RuleCreate,
    RuleListRequest,
    RuleListResponse,
    RuleOut,
    RuleUpdate,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _validation_message(exc: ValidationError) -> str:
    """只带字段路径与原因，不回显提交的值（错误契约：校验错误不回显请求内容）。"""
    parts = []
    for err in exc.errors()[:5]:
        loc = ".".join(str(x) for x in err.get("loc", ()))
        msg = str(err.get("msg", "")).removeprefix("Value error, ")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "费用规则内容不合法：" + "；".join(parts)


def validate_payload(kind: str, scope_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        model = rs.parse_payload(kind, payload)
    except ValidationError as exc:
        raise RuleInvalidError(_validation_message(exc)) from None
    except (TypeError, ValueError) as exc:
        raise RuleInvalidError(f"费用规则内容不合法：{exc}") from None
    problem = rs.check_scope(kind, scope_key, model)
    if problem:
        raise RuleInvalidError(f"费用规则内容不合法：{problem}")
    return rs.dump_payload(model)


def _out(r: PricingRule) -> RuleOut:
    return RuleOut.model_validate(r, from_attributes=True)


async def _get(db: AsyncSession, rule_id: int, *, lock: bool = False) -> PricingRule:
    stmt = select(PricingRule).where(PricingRule.id == rule_id)
    if lock:
        stmt = stmt.with_for_update()
    rule = (await db.execute(stmt)).scalar_one_or_none()
    if rule is None:
        raise RuleNotFoundError()
    return rule


def _check_lock(rule: PricingRule, lock_version: int) -> None:
    if rule.lock_version != lock_version:
        raise RuleConflictError(log_detail=f"rule={rule.id} lock {rule.lock_version}!={lock_version}")


# ---------------------------------------------------------------- 查询


async def list_rules(db: AsyncSession, req: RuleListRequest) -> RuleListResponse:
    conds = []
    if req.kind:
        conds.append(PricingRule.kind == req.kind)
    if req.scope_key:
        conds.append(PricingRule.scope_key == req.scope_key)
    if req.status:
        conds.append(PricingRule.status == req.status)
    total = (await db.execute(select(func.count()).select_from(PricingRule).where(*conds))).scalar_one()
    rows = (await db.execute(
        select(PricingRule).where(*conds)
        .order_by(PricingRule.kind, PricingRule.scope_key, PricingRule.version.desc())
        .offset((req.page - 1) * req.page_size).limit(req.page_size)
    )).scalars().all()
    return RuleListResponse(items=[_out(r) for r in rows], total=total, page=req.page, page_size=req.page_size)


async def get_rule(db: AsyncSession, rule_id: int) -> RuleOut:
    return _out(await _get(db, rule_id))


# ---------------------------------------------------------------- 写入


async def create_draft(db: AsyncSession, user_id: int, req: RuleCreate) -> RuleOut:
    if req.effective_to is not None and req.effective_to < req.effective_from:
        raise RuleInvalidError("费用规则内容不合法：失效日早于生效日")
    payload = validate_payload(req.kind, req.scope_key, req.payload)
    for _ in range(3):
        version = (await db.execute(
            select(func.coalesce(func.max(PricingRule.version), 0))
            .where(PricingRule.kind == req.kind, PricingRule.scope_key == req.scope_key)
        )).scalar_one() + 1
        rule = PricingRule(kind=req.kind, scope_key=req.scope_key, version=version, status="draft",
                           effective_from=req.effective_from, effective_to=req.effective_to, source=req.source,
                           review_note=req.review_note, payload=payload, payload_schema=rs.PAYLOAD_SCHEMA,
                           created_by=user_id, updated_by=user_id)
        db.add(rule)
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            if "uq_pricing_rule_version" not in str(exc.orig):
                raise
            continue
        await db.refresh(rule)
        return _out(rule)
    raise RuleConflictError(log_detail="版本号竞争三次失败")


async def update_draft(db: AsyncSession, user_id: int, rule_id: int, req: RuleUpdate) -> RuleOut:
    rule = await _get(db, rule_id, lock=True)
    if rule.status != "draft":
        raise RuleImmutableError()
    _check_lock(rule, req.lock_version)
    if req.payload is not None:
        rule.payload = validate_payload(rule.kind, rule.scope_key, req.payload)
    if req.effective_from is not None:
        rule.effective_from = req.effective_from
    if req.clear_effective_to:
        rule.effective_to = None
    elif req.effective_to is not None:
        rule.effective_to = req.effective_to
    if rule.effective_to is not None and rule.effective_to < rule.effective_from:
        raise RuleInvalidError("费用规则内容不合法：失效日早于生效日")
    if req.source is not None:
        rule.source = req.source
    if req.review_note is not None:
        rule.review_note = req.review_note
    rule.lock_version += 1
    rule.updated_by = user_id
    rule.updated_at = _now()
    await db.commit()
    await db.refresh(rule)
    return _out(rule)


async def copy_as_draft(db: AsyncSession, user_id: int, rule_id: int) -> RuleOut:
    src = await _get(db, rule_id)
    req = RuleCreate(kind=src.kind, scope_key=src.scope_key,  # type: ignore[arg-type]
                     effective_from=max(src.effective_from, date.today()), effective_to=None,
                     source=src.source, review_note=src.review_note, payload=src.payload)
    return await create_draft(db, user_id, req)


async def delete_draft(db: AsyncSession, rule_id: int, lock_version: int) -> None:
    """只删从未启用过的草稿。草稿从不参与计算，所以不会被任何快照引用。"""
    rule = await _get(db, rule_id, lock=True)
    if rule.status != "draft":
        raise RuleImmutableError("已启用或停用的规则不能删除，只能停用")
    _check_lock(rule, lock_version)
    await db.delete(rule)
    await db.commit()


def _overlaps(a_from: date, a_to: date | None, b_from: date, b_to: date | None) -> bool:
    return (a_to is None or b_from <= a_to) and (b_to is None or a_from <= b_to)


async def activate(db: AsyncSession, user_id: int, rule_id: int, lock_version: int) -> RuleOut:
    rule = await _get(db, rule_id, lock=True)
    if rule.status != "draft":
        raise RuleImmutableError("只有草稿可以启用")
    _check_lock(rule, lock_version)
    if not rule.source.strip():
        raise RuleInvalidError("费用规则内容不合法：来源必填")
    validate_payload(rule.kind, rule.scope_key, rule.payload)

    actives = (await db.execute(
        select(PricingRule).where(PricingRule.kind == rule.kind, PricingRule.scope_key == rule.scope_key,
                                  PricingRule.status == "active").with_for_update()
    )).scalars().all()
    clash = [a for a in actives if _overlaps(a.effective_from, a.effective_to, rule.effective_from, rule.effective_to)]
    if clash:
        raise RuleOverlapError(
            f"与已启用的 v{clash[0].version}（{clash[0].effective_from} 起）生效期重叠，请先为旧版本设定失效日",
        )
    rule.status = "active"
    rule.activated_at = _now()
    rule.lock_version += 1
    rule.updated_by = user_id
    rule.updated_at = _now()
    await db.commit()
    await db.refresh(rule)
    return _out(rule)


async def set_active_end(db: AsyncSession, user_id: int, rule_id: int, lock_version: int, end: date) -> RuleOut:
    """给已启用版本设定失效日——为新版本让出生效期的唯一合法改动，不改载荷。"""
    rule = await _get(db, rule_id, lock=True)
    if rule.status != "active":
        raise RuleImmutableError("只有已启用的规则可以设定失效日")
    _check_lock(rule, lock_version)
    if end < rule.effective_from:
        raise RuleInvalidError("费用规则内容不合法：失效日早于生效日")
    rule.effective_to = end
    rule.lock_version += 1
    rule.updated_by = user_id
    rule.updated_at = _now()
    await db.commit()
    await db.refresh(rule)
    return _out(rule)


async def retire(db: AsyncSession, user_id: int, rule_id: int, lock_version: int, reason: str) -> RuleOut:
    rule = await _get(db, rule_id, lock=True)
    if rule.status != "active":
        raise RuleImmutableError("只有已启用的规则可以停用")
    _check_lock(rule, lock_version)
    rule.status = "retired"
    rule.retired_reason = reason
    rule.retired_at = _now()
    rule.lock_version += 1
    rule.updated_by = user_id
    rule.updated_at = _now()
    await db.commit()
    await db.refresh(rule)
    return _out(rule)


# ---------------------------------------------------------------- 解析


def _ref(r: PricingRule) -> t.RuleRef:
    return t.RuleRef(kind=r.kind, rule_id=r.id, version=r.version, scope_key=r.scope_key, source=r.source)


def ref_snapshot(r: PricingRule) -> dict[str, Any]:
    return {"kind": r.kind, "rule_id": r.id, "version": r.version, "scope_key": r.scope_key,
            "source": r.source, "effective_from": r.effective_from.isoformat(),
            "effective_to": r.effective_to.isoformat() if r.effective_to else None, "payload": r.payload}


async def active_rules_on(db: AsyncSession, on: date, *, markets: list[str] | None = None) -> list[PricingRule]:
    stmt = select(PricingRule).where(
        PricingRule.status == "active", PricingRule.effective_from <= on,
        (PricingRule.effective_to.is_(None)) | (PricingRule.effective_to >= on),
    )
    rows = list((await db.execute(stmt)).scalars().all())
    if markets is None:
        return rows
    currencies = {SITE_CATALOG[m].currency for m in markets}
    keep = []
    for r in rows:
        if r.kind == "fx_rate":
            if r.scope_key in currencies:
                keep.append(r)
        elif r.kind == "fbm_channel":
            if r.scope_key.split(":", 1)[0] in markets:
                keep.append(r)
        elif r.scope_key in markets:
            keep.append(r)
    return keep


async def resolve(db: AsyncSession, markets: list[str], on: date) -> tuple[dict[str, t.MarketRules], list[PricingRule]]:
    """按核价日期解析每个市场命中的规则。返回引擎规则与实际采用的规则行（供快照）。"""
    rows = await active_rules_on(db, on, markets=markets)
    by_key: dict[tuple[str, str], list[PricingRule]] = {}
    for r in rows:
        by_key.setdefault((r.kind, r.scope_key), []).append(r)
    for key, group in by_key.items():
        if len(group) > 1:  # 启用互斥本应拦住；出现即为数据异常，不随机取一条
            raise RuleOverlapError(log_detail=f"多条启用规则同时生效 {key}: {[g.id for g in group]}")

    used: list[PricingRule] = []
    out: dict[str, t.MarketRules] = {}
    for site in markets:
        info = SITE_CATALOG[site]
        minor = Decimal(info.currency_minor_unit)

        def one(kind: str, scope: str) -> PricingRule | None:
            got = by_key.get((kind, scope))
            return got[0] if got else None

        mp_row = one("market_params", site)
        fx_row = one("fx_rate", info.currency)
        ref_row = one("referral_schedule", site)
        fba_row = one("fba_rate_card", site)
        ch_rows = sorted((g[0] for (k, s), g in by_key.items() if k == "fbm_channel" and s.startswith(f"{site}:")),
                         key=lambda r: r.scope_key)
        for r in (mp_row, fx_row, ref_row, fba_row, *ch_rows):
            if r is not None and r not in used:
                used.append(r)

        params = None
        if mp_row is not None:
            params = rs.to_market_params(rs.parse_payload("market_params", mp_row.payload, mp_row.payload_schema),
                                         site, info.currency, minor, _ref(mp_row))  # type: ignore[arg-type]
        fx = None
        if fx_row is not None:
            fx = rs.parse_payload("fx_rate", fx_row.payload, fx_row.payload_schema).cny_per_unit  # type: ignore[attr-defined]
        referral = None
        if ref_row is not None:
            referral = rs.to_referral(rs.parse_payload("referral_schedule", ref_row.payload,  # type: ignore[arg-type]
                                                       ref_row.payload_schema), _ref(ref_row))
        fba = None
        if fba_row is not None:
            fba = rs.to_fba(rs.parse_payload("fba_rate_card", fba_row.payload,  # type: ignore[arg-type]
                                             fba_row.payload_schema), _ref(fba_row))
        channels = tuple(rs.to_fbm(rs.parse_payload("fbm_channel", r.payload, r.payload_schema),  # type: ignore[arg-type]
                                   _ref(r)) for r in ch_rows)
        out[site] = t.MarketRules(site=site, params=params, fx_rate=fx, fx_ref=_ref(fx_row) if fx_row else None,
                                  referral=referral, fba=fba, fbm_channels=channels)
    return out, used


async def configured_markets(db: AsyncSession, on: date) -> dict[str, list[str]]:
    """每个已登记站点缺哪些规则类别（空列表 = 已配齐必需项）。供核价页标记「未配置」。"""
    rows = await active_rules_on(db, on)
    have = {(r.kind, r.scope_key) for r in rows}
    out: dict[str, list[str]] = {}
    for code, site in SITE_CATALOG.items():
        missing = []
        if ("market_params", code) not in have:
            missing.append("market_params")
        if ("fx_rate", site.currency) not in have:
            missing.append("fx_rate")
        if ("referral_schedule", code) not in have:
            missing.append("referral_schedule")
        out[code] = missing
    return out


def rules_updated(snapshot_refs: list[dict[str, Any]], current: list[PricingRule]) -> bool:
    """快照引用的规则里，是否有已不再是当前生效版本的（被停用或被新版本取代）。"""
    current_by_key = {(r.kind, r.scope_key): r.id for r in current}
    return any(current_by_key.get((ref["kind"], ref["scope_key"])) != ref["rule_id"] for ref in snapshot_refs)
