"""定价核算 API（change add-pricing-calculation D10、D11）。瘦控制器：调 service。

- 试算：``POST /pricing/calculate``（无状态，不落库）
- 核价单：``POST /pricing/sheets/list``、``POST /pricing/sheets``、``GET/PATCH/DELETE /pricing/sheets/{id}``、
  ``POST /pricing/sheets/{id}/copy``、``POST /pricing/sheets/{id}/versions``、
  ``GET /pricing/sheets/{id}/versions/{vid}``、``GET /pricing/sheets/{id}/versions/{vid}/export``、
  ``PUT/DELETE /pricing/sheets/{id}/selections/{market}``
- 规则：``POST /pricing/rules/list``、``GET /pricing/rules/{id}``、``POST /pricing/rules``、
  ``PATCH/DELETE /pricing/rules/{id}``、``POST /pricing/rules/{id}/activate|retire|copy|end``
- 元数据：``GET /pricing/meta``

**没有全局鉴权中间件**，每个端点逐个挂依赖。读规则只要 ``pricing:view``，写规则要
``pricing:manage-rates``。核价单的私有性由 service 每条查询带 ``owner_id`` 保证。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import PRICING_MANAGE_RATES, PRICING_VIEW
from app.auth.simple_auth import simple_auth
from app.site_registry import SITE_CATALOG
from app.table.pricing import rule_schemas, rule_service, sheet_service
from app.table.pricing.pricing_schemas import (
    CalcResponse,
    PricingInput,
    RuleAction,
    RuleCreate,
    RuleListRequest,
    RuleListResponse,
    RuleOut,
    RuleRetire,
    RuleUpdate,
    SaveVersionRequest,
    SelectRequest,
    SheetCreate,
    SheetListRequest,
    SheetListResponse,
    SheetOut,
    SheetUpdate,
    VersionOut,
)
from database import get_async_db

pricing_router = APIRouter()

_require_view = simple_auth.require_permission(PRICING_VIEW)
_require_manage = simple_auth.require_permission(PRICING_MANAGE_RATES)


class RuleEnd(BaseModel):
    lock_version: int
    effective_to: date


# ---------------------------------------------------------------- 元数据与试算


@pricing_router.get("/meta")
async def meta(db: AsyncSession = Depends(get_async_db), user=Depends(_require_view)) -> dict[str, Any]:
    """市场（含未配齐的规则类别）、货物属性、各市场当前可选类目。"""
    on = sheet_service.today()
    missing = await rule_service.configured_markets(db, on)
    rules, _ = await rule_service.resolve(db, list(SITE_CATALOG), on)
    markets = []
    for code, site in SITE_CATALOG.items():
        ref = rules[code].referral
        markets.append({
            "code": code, "name": site.name_zh, "currency": site.currency, "missing": missing[code],
            "categories": [{"key": c.key, "name": c.name} for c in ref.categories.values()] if ref else [],
            "fbm_channels": len(rules[code].fbm_channels), "has_fba": rules[code].fba is not None,
            "price_includes_vat": rules[code].params.price_includes_vat if rules[code].params else None,
        })
    return {
        "today": on.isoformat(),
        "markets": markets,
        "attributes": [{"key": k, "name": v} for k, v in rule_schemas.CARGO_ATTRIBUTES.items()],
        "rule_kinds": [{"key": k, "name": v} for k, v in rule_schemas.KIND_LABELS.items()],
        "can_manage_rates": PRICING_MANAGE_RATES in getattr(user, "permission_codes", set())
        or bool(getattr(user, "is_super", False)),
    }


@pricing_router.post("/calculate", response_model=CalcResponse)
async def calculate(req: PricingInput, db: AsyncSession = Depends(get_async_db),
                    user=Depends(_require_view)) -> CalcResponse:
    return await sheet_service.calculate_only(db, req)


# ---------------------------------------------------------------- 核价单


@pricing_router.post("/sheets/list", response_model=SheetListResponse)
async def list_sheets(req: SheetListRequest, db: AsyncSession = Depends(get_async_db),
                      user=Depends(_require_view)) -> SheetListResponse:
    return await sheet_service.list_sheets(db, user.id, req)


@pricing_router.post("/sheets", response_model=SheetOut)
async def create_sheet(req: SheetCreate, db: AsyncSession = Depends(get_async_db),
                       user=Depends(_require_view)) -> SheetOut:
    return await sheet_service.create_sheet(db, user.id, req)


@pricing_router.get("/sheets/{sheet_id}", response_model=SheetOut)
async def get_sheet(sheet_id: int, db: AsyncSession = Depends(get_async_db),
                    user=Depends(_require_view)) -> SheetOut:
    return await sheet_service.get_sheet(db, user.id, sheet_id)


@pricing_router.patch("/sheets/{sheet_id}", response_model=SheetOut)
async def update_sheet(sheet_id: int, req: SheetUpdate, db: AsyncSession = Depends(get_async_db),
                       user=Depends(_require_view)) -> SheetOut:
    return await sheet_service.update_sheet(db, user.id, sheet_id, req)


@pricing_router.delete("/sheets/{sheet_id}")
async def delete_sheet(sheet_id: int, db: AsyncSession = Depends(get_async_db),
                       user=Depends(_require_view)) -> dict[str, bool]:
    await sheet_service.delete_sheet(db, user.id, sheet_id)
    return {"ok": True}


@pricing_router.post("/sheets/{sheet_id}/copy", response_model=SheetOut)
async def copy_sheet(sheet_id: int, db: AsyncSession = Depends(get_async_db),
                     user=Depends(_require_view)) -> SheetOut:
    return await sheet_service.copy_sheet(db, user.id, sheet_id)


@pricing_router.post("/sheets/{sheet_id}/versions", response_model=VersionOut)
async def save_version(sheet_id: int, req: SaveVersionRequest, db: AsyncSession = Depends(get_async_db),
                       user=Depends(_require_view)) -> VersionOut:
    return await sheet_service.save_version(db, user.id, sheet_id, req)


@pricing_router.get("/sheets/{sheet_id}/versions/{version_id}", response_model=VersionOut)
async def get_version(sheet_id: int, version_id: int, db: AsyncSession = Depends(get_async_db),
                      user=Depends(_require_view)) -> VersionOut:
    return await sheet_service.get_version(db, user.id, sheet_id, version_id)


@pricing_router.get("/sheets/{sheet_id}/versions/{version_id}/export")
async def export_version(sheet_id: int, version_id: int, db: AsyncSession = Depends(get_async_db),
                         user=Depends(_require_view)) -> StreamingResponse:
    filename, rows = await sheet_service.prepare_export(db, user.id, sheet_id, version_id)
    return StreamingResponse(sheet_service.iter_csv(rows), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@pricing_router.put("/sheets/{sheet_id}/selections/{market}", response_model=SheetOut)
async def select_option(sheet_id: int, market: str, req: SelectRequest, db: AsyncSession = Depends(get_async_db),
                        user=Depends(_require_view)) -> SheetOut:
    return await sheet_service.select_option(db, user.id, sheet_id, market, req)


@pricing_router.delete("/sheets/{sheet_id}/selections/{market}", response_model=SheetOut)
async def clear_selection(sheet_id: int, market: str, db: AsyncSession = Depends(get_async_db),
                          user=Depends(_require_view)) -> SheetOut:
    return await sheet_service.clear_selection(db, user.id, sheet_id, market)


# ---------------------------------------------------------------- 规则


@pricing_router.post("/rules/list", response_model=RuleListResponse)
async def list_rules(req: RuleListRequest, db: AsyncSession = Depends(get_async_db),
                     user=Depends(_require_view)) -> RuleListResponse:
    return await rule_service.list_rules(db, req)


@pricing_router.get("/rules/{rule_id}", response_model=RuleOut)
async def get_rule(rule_id: int, db: AsyncSession = Depends(get_async_db), user=Depends(_require_view)) -> RuleOut:
    return await rule_service.get_rule(db, rule_id)


@pricing_router.post("/rules", response_model=RuleOut)
async def create_rule(req: RuleCreate, db: AsyncSession = Depends(get_async_db),
                      user=Depends(_require_manage)) -> RuleOut:
    return await rule_service.create_draft(db, user.id, req)


@pricing_router.patch("/rules/{rule_id}", response_model=RuleOut)
async def update_rule(rule_id: int, req: RuleUpdate, db: AsyncSession = Depends(get_async_db),
                      user=Depends(_require_manage)) -> RuleOut:
    return await rule_service.update_draft(db, user.id, rule_id, req)


@pricing_router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: int, lock_version: int, db: AsyncSession = Depends(get_async_db),
                      user=Depends(_require_manage)) -> dict[str, bool]:
    await rule_service.delete_draft(db, rule_id, lock_version)
    return {"ok": True}


@pricing_router.post("/rules/{rule_id}/activate", response_model=RuleOut)
async def activate_rule(rule_id: int, req: RuleAction, db: AsyncSession = Depends(get_async_db),
                        user=Depends(_require_manage)) -> RuleOut:
    return await rule_service.activate(db, user.id, rule_id, req.lock_version)


@pricing_router.post("/rules/{rule_id}/end", response_model=RuleOut)
async def end_rule(rule_id: int, req: RuleEnd, db: AsyncSession = Depends(get_async_db),
                   user=Depends(_require_manage)) -> RuleOut:
    return await rule_service.set_active_end(db, user.id, rule_id, req.lock_version, req.effective_to)


@pricing_router.post("/rules/{rule_id}/retire", response_model=RuleOut)
async def retire_rule(rule_id: int, req: RuleRetire, db: AsyncSession = Depends(get_async_db),
                      user=Depends(_require_manage)) -> RuleOut:
    return await rule_service.retire(db, user.id, rule_id, req.lock_version, req.reason)


@pricing_router.post("/rules/{rule_id}/copy", response_model=RuleOut)
async def copy_rule(rule_id: int, db: AsyncSession = Depends(get_async_db),
                    user=Depends(_require_manage)) -> RuleOut:
    return await rule_service.copy_as_draft(db, user.id, rule_id)
