"""核价单：试算、草稿、不可变版本、复制、选定与导出（design D9、D10）。

两条纪律：

1. **私有性在每条查询里**：所有读写都带 ``owner_id = 当前用户``，取不到一律 404
   ``pricing.sheet_not_found``，不区分「不存在」与「不是你的」。
2. **保存时服务端重算**：版本快照的结果永远来自引擎，不接受客户端传来的数字。
"""

from __future__ import annotations

import csv
import io
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.table.pricing import rule_service
from app.table.pricing.engine import calculate
from app.table.pricing.pricing_errors import (
    OptionNotSelectableError,
    SheetConflictError,
    SheetNotFoundError,
    VersionNotCurrentError,
    VersionNotFoundError,
)
from app.table.pricing.pricing_model import (
    PricingSelection,
    PricingSelectionLog,
    PricingSheet,
    PricingSheetVersion,
)
from app.table.pricing.pricing_schemas import (
    CalcResponse,
    PricingInput,
    SaveVersionRequest,
    SelectionOut,
    SelectRequest,
    SheetCreate,
    SheetListRequest,
    SheetListResponse,
    SheetOut,
    SheetUpdate,
    VersionBrief,
    VersionOut,
    to_jsonable,
)

#: 核价日期按团队所在时区取「今天」：规则按日生效，跨零点时服务器 UTC 日期会差一天。
_TZ = ZoneInfo("Asia/Shanghai")


def today() -> date:
    return datetime.now(_TZ).date()


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- 试算


async def compute(db: AsyncSession, inp: PricingInput, on: date) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rules, used = await rule_service.resolve(db, inp.markets, on)
    result = calculate(inp.to_scenario(on), rules)
    return to_jsonable(result), [rule_service.ref_snapshot(r) for r in used]


async def calculate_only(db: AsyncSession, inp: PricingInput) -> CalcResponse:
    on = today()
    result, refs = await compute(db, inp, on)
    return CalcResponse(calc_date=on, result=result, rule_refs=refs)


# ---------------------------------------------------------------- 核价单


async def _sheet(db: AsyncSession, owner_id: int, sheet_id: int, *, lock: bool = False) -> PricingSheet:
    stmt = select(PricingSheet).where(PricingSheet.id == sheet_id, PricingSheet.owner_id == owner_id)
    if lock:
        stmt = stmt.with_for_update()
    sheet = (await db.execute(stmt)).scalar_one_or_none()
    if sheet is None:
        raise SheetNotFoundError(log_detail=f"sheet={sheet_id} owner={owner_id}")
    return sheet


def _markets_of(sheet: PricingSheet) -> list[str]:
    markets = (sheet.draft_input or {}).get("markets")
    return [m for m in markets if isinstance(m, str)] if isinstance(markets, list) else []


async def _out(db: AsyncSession, sheet: PricingSheet, *, detail: bool,
               current_rules: list | None = None) -> SheetOut:
    updated = False
    if sheet.current_version_id is not None:
        ver = await db.get(PricingSheetVersion, sheet.current_version_id)
        if ver is not None:
            if current_rules is None:
                current_rules = await rule_service.active_rules_on(db, today())
            updated = rule_service.rules_updated(ver.rule_refs, current_rules)
    out = SheetOut(
        id=sheet.id, product_name=sheet.product_name, sku=sheet.sku, asin=sheet.asin, source_url=sheet.source_url,
        status=sheet.status, draft_input=sheet.draft_input, current_version_id=sheet.current_version_id,
        lock_version=sheet.lock_version, created_at=sheet.created_at, updated_at=sheet.updated_at,
        markets=_markets_of(sheet), rules_updated=updated,
    )
    sels = (await db.execute(select(PricingSelection).where(PricingSelection.sheet_id == sheet.id)
                             .order_by(PricingSelection.market))).scalars().all()
    out.selections = [SelectionOut(market=s.market, version_id=s.version_id, option_key=s.option_key, note=s.note,
                                   selected_at=s.selected_at) for s in sels]
    if detail:
        vers = (await db.execute(select(PricingSheetVersion).where(PricingSheetVersion.sheet_id == sheet.id)
                                 .order_by(PricingSheetVersion.version_no.desc()))).scalars().all()
        out.versions = [VersionBrief(id=v.id, version_no=v.version_no, calc_date=v.calc_date,
                                     model_version=v.model_version, created_at=v.created_at) for v in vers]
    return out


async def list_sheets(db: AsyncSession, owner_id: int, req: SheetListRequest) -> SheetListResponse:
    conds = [PricingSheet.owner_id == owner_id]
    if req.q:
        like = f"%{req.q.strip()}%"
        conds.append(or_(PricingSheet.product_name.ilike(like), PricingSheet.sku.ilike(like),
                         PricingSheet.asin.ilike(like)))
    if req.market:
        conds.append(PricingSheet.draft_input["markets"].contains([req.market.upper()]))
    if req.status:
        conds.append(PricingSheet.status == req.status)
    total = (await db.execute(select(func.count()).select_from(PricingSheet).where(*conds))).scalar_one()
    rows = (await db.execute(select(PricingSheet).where(*conds).order_by(PricingSheet.updated_at.desc(),
                                                                         PricingSheet.id.desc())
                             .offset((req.page - 1) * req.page_size).limit(req.page_size))).scalars().all()
    current = await rule_service.active_rules_on(db, today())
    items = [await _out(db, s, detail=False, current_rules=current) for s in rows]
    return SheetListResponse(items=items, total=total, page=req.page, page_size=req.page_size)


async def get_sheet(db: AsyncSession, owner_id: int, sheet_id: int) -> SheetOut:
    return await _out(db, await _sheet(db, owner_id, sheet_id), detail=True)


async def create_sheet(db: AsyncSession, owner_id: int, req: SheetCreate) -> SheetOut:
    sheet = PricingSheet(owner_id=owner_id, product_name=req.product_name, sku=req.sku, asin=req.asin,
                         source_url=req.source_url, status="draft", draft_input=req.draft_input)
    db.add(sheet)
    await db.commit()
    await db.refresh(sheet)
    return await _out(db, sheet, detail=True)


async def update_sheet(db: AsyncSession, owner_id: int, sheet_id: int, req: SheetUpdate) -> SheetOut:
    sheet = await _sheet(db, owner_id, sheet_id, lock=True)
    if sheet.lock_version != req.lock_version:
        raise SheetConflictError(log_detail=f"sheet={sheet_id} lock {sheet.lock_version}!={req.lock_version}")
    for field in ("product_name", "sku", "asin", "source_url", "draft_input"):
        value = getattr(req, field)
        if field in req.model_fields_set and (value is not None or field != "product_name"):
            setattr(sheet, field, value)
    sheet.lock_version += 1
    sheet.updated_at = _now()
    await db.commit()
    await db.refresh(sheet)
    return await _out(db, sheet, detail=True)


async def delete_sheet(db: AsyncSession, owner_id: int, sheet_id: int) -> None:
    sheet = await _sheet(db, owner_id, sheet_id, lock=True)
    await db.delete(sheet)
    await db.commit()


async def copy_sheet(db: AsyncSession, owner_id: int, sheet_id: int) -> SheetOut:
    src = await _sheet(db, owner_id, sheet_id)
    req = SheetCreate(product_name=f"{src.product_name}（副本）"[:200], sku=src.sku, asin=src.asin,
                      source_url=src.source_url, draft_input=src.draft_input)
    return await create_sheet(db, owner_id, req)


def _version_out(v: PricingSheetVersion) -> VersionOut:
    return VersionOut(id=v.id, sheet_id=v.sheet_id, version_no=v.version_no, calc_date=v.calc_date,
                      model_version=v.model_version, input=v.input, rule_refs=v.rule_refs, result=v.result,
                      created_at=v.created_at)


async def _existing_token(db: AsyncSession, sheet_id: int, token: str) -> PricingSheetVersion | None:
    return (await db.execute(select(PricingSheetVersion).where(
        PricingSheetVersion.sheet_id == sheet_id, PricingSheetVersion.client_token == token))).scalar_one_or_none()


async def save_version(db: AsyncSession, owner_id: int, sheet_id: int, req: SaveVersionRequest) -> VersionOut:
    """保存为新版本：服务端按输入重算，生成不可变快照。同一 ``client_token`` 只生成一次。"""
    sheet = await _sheet(db, owner_id, sheet_id, lock=True)
    dup = await _existing_token(db, sheet_id, req.client_token)
    if dup is not None:
        return _version_out(dup)
    if sheet.lock_version != req.lock_version:
        raise SheetConflictError(log_detail=f"sheet={sheet_id} lock {sheet.lock_version}!={req.lock_version}")

    on = today()
    result, refs = await compute(db, req.input, on)
    next_no = (await db.execute(select(func.coalesce(func.max(PricingSheetVersion.version_no), 0))
                                .where(PricingSheetVersion.sheet_id == sheet_id))).scalar_one() + 1
    from app.table.pricing.engine import MODEL_VERSION

    ver = PricingSheetVersion(sheet_id=sheet_id, version_no=next_no, input=req.input.model_dump(mode="json"),
                              rule_refs=refs, result=result, model_version=MODEL_VERSION, calc_date=on,
                              client_token=req.client_token, created_by=owner_id)
    db.add(ver)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        dup = await _existing_token(db, sheet_id, req.client_token)
        if dup is not None:
            return _version_out(dup)
        raise SheetConflictError(log_detail=f"sheet={sheet_id} 版本号竞争") from None
    sheet.current_version_id = ver.id
    sheet.status = "saved"
    sheet.draft_input = ver.input
    sheet.lock_version += 1
    sheet.updated_at = _now()
    await db.commit()
    await db.refresh(ver)
    return _version_out(ver)


async def get_version(db: AsyncSession, owner_id: int, sheet_id: int, version_id: int) -> VersionOut:
    await _sheet(db, owner_id, sheet_id)
    ver = (await db.execute(select(PricingSheetVersion).where(
        PricingSheetVersion.id == version_id, PricingSheetVersion.sheet_id == sheet_id))).scalar_one_or_none()
    if ver is None:
        raise VersionNotFoundError()
    return _version_out(ver)


def _find_option(result: dict[str, Any], market: str, option_key: str) -> dict[str, Any] | None:
    for m in result.get("markets", []):
        if m.get("market") != market:
            continue
        for o in m.get("options", []):
            if o.get("option_key") == option_key:
                return o
    return None


def selectable(option: dict[str, Any] | None) -> bool:
    return (option is not None and option.get("availability") == "available"
            and option.get("evaluation") is not None and option.get("solve_status") != "unsolvable")


async def select_option(db: AsyncSession, owner_id: int, sheet_id: int, market: str,
                        req: SelectRequest) -> SheetOut:
    sheet = await _sheet(db, owner_id, sheet_id, lock=True)
    market = market.upper()
    if sheet.current_version_id != req.version_id:
        raise VersionNotCurrentError()
    ver = await db.get(PricingSheetVersion, req.version_id)
    if ver is None or ver.sheet_id != sheet_id:
        raise VersionNotFoundError()
    if not selectable(_find_option(ver.result, market, req.option_key)):
        raise OptionNotSelectableError()
    existing = await db.get(PricingSelection, (sheet_id, market))
    if existing is None:
        db.add(PricingSelection(sheet_id=sheet_id, market=market, version_id=ver.id, option_key=req.option_key,
                                note=req.note, selected_by=owner_id))
    else:
        existing.version_id = ver.id
        existing.option_key = req.option_key
        existing.note = req.note
        existing.selected_by = owner_id
        existing.selected_at = _now()
    db.add(PricingSelectionLog(sheet_id=sheet_id, market=market, version_id=ver.id, option_key=req.option_key,
                               note=req.note, selected_by=owner_id))
    sheet.updated_at = _now()
    await db.commit()
    await db.refresh(sheet)
    return await _out(db, sheet, detail=True)


async def clear_selection(db: AsyncSession, owner_id: int, sheet_id: int, market: str) -> SheetOut:
    sheet = await _sheet(db, owner_id, sheet_id, lock=True)
    existing = await db.get(PricingSelection, (sheet_id, market.upper()))
    if existing is not None:
        await db.delete(existing)
        sheet.updated_at = _now()
        await db.commit()
        await db.refresh(sheet)
    return await _out(db, sheet, detail=True)


# ---------------------------------------------------------------- 导出


_STATUS_TEXT = {"available": "可用", "not_applicable": "不适用", "needs_input": "待补资料",
                "missing_config": "缺少费用配置"}
_SOLVE_TEXT = {"met": "达标", "not_met": "未达标", "unsolvable": "无法达到目标", "not_computed": ""}


async def prepare_export(db: AsyncSession, owner_id: int, sheet_id: int,
                         version_id: int) -> tuple[str, list[list[str]]]:
    """在开流前完成全部校验与取数（错误契约：流式端点的失败在开流前判定）。"""
    sheet = await _sheet(db, owner_id, sheet_id)
    ver = await get_version(db, owner_id, sheet_id, version_id)
    sels = {s.market: s.option_key for s in (await db.execute(
        select(PricingSelection).where(PricingSelection.sheet_id == sheet_id))).scalars().all()
        if s.version_id == version_id}
    rows: list[list[str]] = [
        ["产品", sheet.product_name, "SKU", sheet.sku or "", "ASIN", sheet.asin or ""],
        ["核价版本", f"v{ver.version_no}", "核价日期", ver.calc_date.isoformat(), "模型版本", ver.model_version],
        ["核价方式", str(ver.input.get("mode")), "采购成本(CNY)", str(ver.input.get("purchase_cost") or ""),
         "包装人工(CNY)", str(ver.input.get("packaging_labor") or "")],
        ["重量(kg)", str(ver.input.get("weight_kg") or ""), "尺寸(cm)",
         "×".join(str(ver.input.get(k) or "?") for k in ("length_cm", "width_cm", "height_cm"))],
        [],
        ["市场", "币种", "汇率(CNY/单位)", "方案", "状态", "原因", "选定", "售价", "保本售价", "采购价上限(CNY)",
         "预计单件利润(CNY)", "利润率", "投资回报率", "目标", "目标差距(CNY)"],
    ]
    detail: list[list[str]] = [[], ["费用明细"], ["市场", "方案", "费用项", "金额(CNY)", "原币金额", "币种",
                                                  "计费依据", "规则", "规则来源"]]
    for m in ver.result.get("markets", []):
        if not m.get("options"):
            rows.append([m["market"], m.get("currency", ""), "", "", m.get("status", ""),
                         "；".join(m.get("reasons", [])), "", "", "", "", "", "", "", "", ""])
            continue
        for o in m["options"]:
            ev = o.get("evaluation") or {}
            rows.append([
                m["market"], m.get("currency", ""), m.get("fx_rate") or "", o["label"],
                _STATUS_TEXT.get(o["availability"], o["availability"]), "；".join(o.get("reasons", [])),
                "是" if sels.get(m["market"]) == o["option_key"] else "", ev.get("price", ""),
                o.get("breakeven_price") or "", o.get("max_purchase_cost") or "", ev.get("profit_cny", ""),
                ev.get("margin") or "", ev.get("roi") or "", _SOLVE_TEXT.get(o.get("solve_status", ""), ""),
                o.get("target_gap_cny") or "",
            ])
            for ln in ev.get("lines", []):
                rule = ln.get("rule") or {}
                detail.append([m["market"], o["label"], ln["label"], ln["amount_cny"], ln.get("amount_orig") or "",
                               ln.get("currency", ""), ln.get("basis", ""),
                               f"{rule.get('kind')} v{rule.get('version')}" if rule else "", rule.get("source", "")])
    rows += detail
    rows += [[], ["未纳入的费用", "；".join(ver.result.get("excluded_fees", []))]]
    filename = f"pricing_{sheet_id}_v{ver.version_no}.csv"
    return filename, rows


async def iter_csv(rows: list[list[str]]) -> AsyncIterator[bytes]:
    buf = io.StringIO()
    buf.write("﻿")
    writer = csv.writer(buf)
    for row in rows:
        writer.writerow(row)
    yield buf.getvalue().encode("utf-8")
