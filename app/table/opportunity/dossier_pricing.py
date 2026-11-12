"""机会档案的商业可行性：关联核价单与测算摘要（change add-opportunity-feasibility）。

档案不复制测算，只持有关联关系（``pricing_sheet.dossier_id``）。摘要只取核价单**已保存版本**里
核价引擎算出的值，字段走白名单（design 决策 2）：采购成本、费用明细、输入参数是创建人的私有输入，
不出现在摘要里；核价单明细仍按创建人私有。

金额原样取自版本快照里的十进制字符串，这里不计算、不换算——金额只有核价引擎一个来源。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.table.opportunity.research_service import _user_names
from app.table.pricing import rule_service, sheet_service
from app.table.pricing.pricing_errors import SheetNotFoundError
from app.table.pricing.pricing_model import PricingSelection, PricingSheet, PricingSheetVersion
from app.table.pricing.pricing_schemas import SheetCreate

#: 摘要里一个方案公开的字段（金额都是快照里的原始字符串）
_EVAL_FIELDS = ("price", "profit_cny", "margin", "roi")
_OPTION_FIELDS = ("breakeven_price", "max_purchase_cost", "target_price", "target_gap_cny")

MARKET_STATUS_TEXT = {
    "ok": "可计算",
    "all_not_applicable": "所有方案都不适用",
    "missing_config": "费用规则未配齐",
    "needs_input": "待补资料",
}

#: 市场整体可算、但没有任何方案算出结果时，按方案层的阻塞原因汇总出的状态（优先级从高到低）
_OPTION_BLOCKERS = (("needs_input", "needs_input"), ("missing_config", "missing_config"),
                    ("not_applicable", "all_not_applicable"))


def _calculable(o: dict[str, Any]) -> bool:
    """算出了可用结果：可用、有正算、且目标不是无解。"""
    return (o.get("availability") == "available" and o.get("evaluation") is not None
            and o.get("solve_status") != "unsolvable")


def _option_brief(o: dict[str, Any]) -> dict[str, Any]:
    ev = o.get("evaluation") or {}
    brief = {
        "option_key": o.get("option_key"), "label": o.get("label"),
        "availability": o.get("availability"), "reasons": list(o.get("reasons") or []),
        "solve_status": o.get("solve_status"), "recommended": bool(o.get("recommended")),
        **{k: ev.get(k) for k in _EVAL_FIELDS},
        **{k: o.get(k) for k in _OPTION_FIELDS},
    }
    adj = o.get("adjusted")
    # 核价单里手动调过这个方案的售价：引擎按调后价重算并判定是否达标（PR #189 复审）。
    # 调价是创建人最终要卖的价格，摘要必须带上，并与原始求解结果分开。
    brief["adjusted"] = None if not adj else {
        "price": adj.get("price"), "profit_cny": adj.get("profit_cny"), "margin": adj.get("margin"),
        "roi": adj.get("roi"), "met": o.get("adjusted_met"), "gap_cny": o.get("adjusted_gap_cny"),
    }
    if o.get("solve_status") == "unsolvable":
        # 目标无解时引擎附带的正算是诊断用的（倒推采购价时按 0 采购成本算），不能当成预计利润展示
        # （PR #189 评审）。售价保留（它是用户给定的），其余金额置空。
        for k in ("profit_cny", "margin", "roi", "max_purchase_cost", "target_gap_cny"):
            brief[k] = None
    return brief


def _pick(market: dict[str, Any], selected_key: str | None) -> tuple[dict[str, Any] | None, bool]:
    """返回 (方案, 是否为选定方案)：选定 → 推荐 → 第一个算出可用结果的方案 → 第一个目标无解的方案。

    无解方案排在最后：它说明「这个渠道达不到目标」，比什么都不显示有用，但不能顶替一个算得出的方案。
    """
    options = market.get("options") or []
    if selected_key:
        hit = next((o for o in options if o.get("option_key") == selected_key), None)
        if hit is not None:
            return hit, True
    for cond in (lambda o: o.get("recommended"), _calculable,
                 lambda o: o.get("availability") == "available" and o.get("solve_status") == "unsolvable"):
        hit = next((o for o in options if cond(o)), None)
        if hit is not None:
            return hit, False
    return None, False


def _blocked_status(market: dict[str, Any]) -> tuple[str, list[str]]:
    """没有方案能给出结果时，从方案层汇总状态与原因。

    引擎在市场配置齐全时把市场状态记为 ok，阻塞原因留在每个方案上（例如所有渠道都缺重量）；
    直接透传 ok 会让摘要显示「可计算」却没有结果、也没有原因（PR #189 评审）。
    """
    options = market.get("options") or []
    for availability, status in _OPTION_BLOCKERS:
        hits = [o for o in options if o.get("availability") == availability]
        if hits:
            reasons = [f"{o.get('label')}：{r}" for o in hits for r in (o.get("reasons") or [])]
            return status, list(dict.fromkeys(reasons))
    return "all_not_applicable", list(market.get("reasons") or [])


def _markets(version: PricingSheetVersion, selections: dict[str, PricingSelection]) -> list[dict[str, Any]]:
    out = []
    for m in (version.result or {}).get("markets", []):
        code = m.get("market")
        sel = selections.get(code)
        # 选定后又保存了新版本：选定仍指向旧版本，摘要以当前版本为准并提示重新选定
        stale = sel is not None and sel.version_id != version.id
        status, reasons = m.get("status"), list(m.get("reasons") or [])
        # 市场整体不可算时不给方案，也就不会有任何金额
        option, selected = _pick(m, None if sel is None or stale else sel.option_key) if status == "ok" else (None, False)
        if status == "ok" and option is None:
            status, reasons = _blocked_status(m)
        out.append({
            "market": code, "currency": m.get("currency"), "status": status,
            "status_text": MARKET_STATUS_TEXT.get(status, status), "reasons": reasons,
            "option": _option_brief(option) if option is not None else None,
            "selected": selected, "selection_stale": stale,
        })
    return out


async def pricing_summary(db: AsyncSession, dossier_id: int) -> dict[str, Any]:
    """档案关联核价单的测算摘要。``state``：none（没有关联）/ draft_only（都只有草稿）/ calculated。"""
    sheets = list((await db.execute(select(PricingSheet).where(PricingSheet.dossier_id == dossier_id)
                                    .order_by(PricingSheet.id))).scalars().all())
    if not sheets:
        return {"state": "none", "sheets": []}
    names = await _user_names(db, {s.owner_id for s in sheets})
    current_rules = await rule_service.active_rules_on(db, sheet_service.today())
    out = []
    for s in sheets:
        item: dict[str, Any] = {
            "id": s.id, "product_name": s.product_name, "owner_id": s.owner_id, "owner_name": names.get(s.owner_id),
            "updated_at": s.updated_at,
        }
        ver = await db.get(PricingSheetVersion, s.current_version_id) if s.current_version_id else None
        if ver is None:
            out.append(item | {"state": "draft_only", "markets": []})
            continue
        sels = {x.market: x for x in (await db.execute(
            select(PricingSelection).where(PricingSelection.sheet_id == s.id))).scalars().all()}
        out.append(item | {
            "state": "calculated", "version_id": ver.id, "version_no": ver.version_no,
            "calc_date": ver.calc_date, "mode": (ver.input or {}).get("mode"),
            "rules_updated": rule_service.rules_updated(ver.rule_refs, current_rules),
            "excluded_fees": list((ver.result or {}).get("excluded_fees") or []),
            "markets": _markets(ver, sels),
        })
    state = "calculated" if any(x["state"] == "calculated" for x in out) else "draft_only"
    return {"state": state, "sheets": out}


def status_for_decision(summary: dict[str, Any]) -> dict[str, Any]:
    """签署决定时固化的测算状态：只留判断所需的字段。之后核价单再保存新版本，这里不变。"""
    return {
        "state": summary["state"],
        "sheets": [{
            "id": s["id"], "product_name": s["product_name"], "state": s["state"],
            "version_no": s.get("version_no"), "calc_date": str(s["calc_date"]) if s.get("calc_date") else None,
            "markets": [{
                "market": m["market"], "currency": m["currency"], "status": m["status"], "selected": m["selected"],
                "solve_status": m["option"]["solve_status"] if m["option"] else None,
                **({k: m["option"][k] for k in ("label", "price", "profit_cny", "margin", "max_purchase_cost",
                                                 "adjusted")}
                   if m["option"] else {}),
            } for m in s.get("markets", [])],
        } for s in summary["sheets"]],
    }


async def _own_sheet(db: AsyncSession, user_id: int, sheet_id: int) -> PricingSheet:
    s = (await db.execute(select(PricingSheet).where(
        PricingSheet.id == sheet_id, PricingSheet.owner_id == user_id).with_for_update())).scalar_one_or_none()
    if s is None:
        # 不存在与不属于当前用户不区分（沿用核价单的私有性）
        raise SheetNotFoundError(log_detail=f"sheet={sheet_id} owner={user_id}")
    return s


async def link(db: AsyncSession, user_id: int, dossier_id: int, sheet_id: int) -> None:
    s = await _own_sheet(db, user_id, sheet_id)
    s.dossier_id = dossier_id


async def unlink(db: AsyncSession, user_id: int, dossier_id: int, sheet_id: int) -> bool:
    s = await _own_sheet(db, user_id, sheet_id)
    if s.dossier_id != dossier_id:
        return False
    s.dossier_id = None
    return True


async def create_draft(db: AsyncSession, user_id: int, dossier_id: int, title: str, site: str) -> PricingSheet:
    """以档案标题为产品名、档案站点为市场建一张草稿并关联。草稿不算结果，用户在快速核价页继续填写。"""
    req = SheetCreate(product_name=title[:200], draft_input={"mode": "target_profit", "markets": [site]})
    s = PricingSheet(owner_id=user_id, product_name=req.product_name, status="draft",
                     draft_input=req.draft_input, dossier_id=dossier_id)
    db.add(s)
    await db.flush()
    return s


async def my_linkable(db: AsyncSession, user_id: int) -> dict[str, Any]:
    rows = (await db.execute(
        select(PricingSheet.id, PricingSheet.product_name, PricingSheet.status, PricingSheet.dossier_id,
               PricingSheet.updated_at)
        .where(PricingSheet.owner_id == user_id)
        .order_by(PricingSheet.updated_at.desc(), PricingSheet.id.desc()).limit(100))).all()
    return {"items": [{"id": i, "product_name": n, "status": st, "dossier_id": did, "updated_at": u}
                      for i, n, st, did, u in rows]}
