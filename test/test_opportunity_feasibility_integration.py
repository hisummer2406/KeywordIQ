"""机会档案 × 核价单（商业可行性）的服务层集成测试（真库）。默认跳过，只在设置 TEST_DATABASE_URL 时运行。

钉住的事（change add-opportunity-feasibility，specs/opportunity-dossier）：
- 摘要只取已保存版本里引擎算出的值：选定方案、未选定取推荐、选定来自旧版本时提示、只有草稿、
  市场配置缺失时不给金额；
- 采购成本、费用明细、输入参数不进摘要；他人看得到摘要、打不开核价单明细；
- 只能关联自己的核价单；从档案建草稿并关联；
- 决定固化测算状态，之后核价单再保存新版本也不变；
- 追问输入带测算摘要，提示词要求金额只引用不计算。
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.table.opportunity import dossier_ask, dossier_service
from app.table.opportunity.dossier_schemas import DecisionCreate, DossierCreate
from app.table.pricing import rule_service, sheet_service
from app.table.pricing.pricing_errors import SheetNotFoundError
from app.table.pricing.pricing_schemas import (
    PricingInput,
    RuleCreate,
    SaveVersionRequest,
    SelectRequest,
    SheetCreate,
)

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)

LONG_AGO = date(2020, 1, 1)
MP = {"price_includes_vat": False, "vat_rate": "0"}
REF = {"categories": [{"key": "general", "name": "通用", "tiers": [{"rate": "0.20"}]}]}
CH = {"site": "US", "code": "C1", "name": "专线", "carrier": "测试", "allowed_attributes": ["general"],
      "brackets": [{"max_weight_kg": "2", "per_parcel_fee": "20", "per_kg": "0"}], "duty_mode": "included"}


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture()
async def db():
    engine = create_async_engine(_async_url(TEST_URL), poolclass=NullPool)
    connection = await engine.connect()
    trans = await connection.begin()
    session = AsyncSession(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        if await session.scalar(text("SELECT to_regclass('analysis.opp_dossier')")) is None:
            pytest.skip("测试库未迁移，请先运行 alembic upgrade head")
        # 共享测试库里可能有别的用例留下的规则：本事务内清空，回滚后恢复
        await session.execute(text("DELETE FROM analysis.pricing_rule"))
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await connection.close()
        await engine.dispose()


async def _user(db, name):
    uid = (await db.execute(text(
        "INSERT INTO analysis.user_center(user_name,hashed_pwd,is_active,is_super) "
        "VALUES (:n,'x',true,false) RETURNING id"), {"n": f"feas-{name}-{uuid.uuid4().hex[:6]}"})).scalar()
    await db.commit()
    return uid


async def _active(db, uid, kind, scope, payload):
    r = await rule_service.create_draft(db, uid, RuleCreate(kind=kind, scope_key=scope, effective_from=LONG_AGO,
                                                            source="测试构造", payload=payload))
    return await rule_service.activate(db, uid, r.id, r.lock_version)


async def _seed_us(db, uid):
    await _active(db, uid, "market_params", "US", MP)
    await _active(db, uid, "fx_rate", "USD", {"currency": "USD", "cny_per_unit": "1"})
    await _active(db, uid, "referral_schedule", "US", REF)
    await _active(db, uid, "fbm_channel", "US:C1", CH)
    await _active(db, uid, "fbm_channel", "US:C2", {**CH, "code": "C2", "name": "贵线",
                                                     "brackets": [{"max_weight_kg": "2", "per_parcel_fee": "30",
                                                                   "per_kg": "0"}]})


def _input(**kw):
    base = {"mode": "target_profit", "markets": ["US"], "purchase_cost": "10", "packaging_labor": "5",
            "weight_kg": "0.1", "category": "general", "target_profit": "40", "include_fba": False}
    base.update(kw)
    return PricingInput.model_validate(base)


async def _dossier(db, uid, title="迷你无线直发梳"):
    return (await dossier_service.create_dossier(db, uid, DossierCreate(
        title=title, site="US", keywords=[f"feasibility kw {uuid.uuid4().hex[:6]}"], client_token=uuid.uuid4().hex)))["id"]


async def _save(db, uid, sheet_id, **kw):
    s = await sheet_service.get_sheet(db, uid, sheet_id)
    return await sheet_service.save_version(db, uid, sheet_id, SaveVersionRequest(
        lock_version=s.lock_version, client_token=uuid.uuid4().hex, input=_input(**kw)))


def _us(summary, i=0):
    return next(m for m in summary["sheets"][i]["markets"] if m["market"] == "US")


async def test_summary_uses_saved_engine_values_and_hides_private_inputs(db):
    owner, other = await _user(db, "a"), await _user(db, "b")
    await _seed_us(db, owner)
    did = await _dossier(db, owner)
    sheet = await sheet_service.create_sheet(db, owner, SheetCreate(product_name="直发梳 核价"))
    await dossier_service.link_pricing_sheet(db, owner, did, sheet.id)

    # 只有草稿
    p = (await dossier_service.get_dossier(db, other, did))["pricing"]
    assert p["state"] == "draft_only" and p["sheets"][0]["state"] == "draft_only" and p["sheets"][0]["markets"] == []

    v1 = await _save(db, owner, sheet.id)
    engine_c1 = next(o for o in v1.result["markets"][0]["options"] if o["option_key"] == "FBM:C1")
    p = (await dossier_service.get_dossier(db, other, did))["pricing"]
    sh = p["sheets"][0]
    assert (p["state"], sh["state"], sh["version_no"], sh["owner_id"], sh["mode"]) == (
        "calculated", "calculated", 1, owner, "target_profit")
    us = _us(p)
    # 未选定：取推荐方案（便宜的 C1），金额原样来自快照
    assert (us["status"], us["selected"], us["selection_stale"]) == ("ok", False, False)
    assert us["option"]["option_key"] == "FBM:C1" and us["option"]["recommended"] is True
    assert us["option"]["price"] == engine_c1["evaluation"]["price"] == "93.75"
    assert us["option"]["profit_cny"] == engine_c1["evaluation"]["profit_cny"] == "40.00"
    assert us["option"]["max_purchase_cost"] == engine_c1["max_purchase_cost"]
    # 私有输入不外泄：采购成本、费用明细、输入都不在摘要里
    blob = json.dumps(p, default=str)
    for leaked in ('"lines"', '"purchase_cost"', '"input"', '"rule_refs"', '"revenue_cny"'):
        assert leaked not in blob
    # 明细仍只有创建人能看
    with pytest.raises(SheetNotFoundError):
        await sheet_service.get_sheet(db, other, sheet.id)

    # 选定贵线 C2 → 摘要取选定方案
    await sheet_service.select_option(db, owner, sheet.id, "US", SelectRequest(version_id=v1.id, option_key="FBM:C2"))
    us = _us((await dossier_service.get_dossier(db, other, did))["pricing"])
    assert (us["selected"], us["option"]["option_key"]) == (True, "FBM:C2")

    # 又保存了新版本：选定仍指向旧版本 → 以当前版本为准，提示重新选定
    await _save(db, owner, sheet.id)
    p = (await dossier_service.get_dossier(db, other, did))["pricing"]
    us = _us(p)
    assert p["sheets"][0]["version_no"] == 2
    assert (us["selected"], us["selection_stale"], us["option"]["option_key"]) == (False, True, "FBM:C1")


async def test_missing_config_market_shows_status_without_amounts(db):
    owner = await _user(db, "a")
    await _seed_us(db, owner)
    did = await _dossier(db, owner)
    sheet = await sheet_service.create_sheet(db, owner, SheetCreate(product_name="P"))
    await dossier_service.link_pricing_sheet(db, owner, did, sheet.id)
    await _save(db, owner, sheet.id, markets=["US", "DE"])
    de = next(m for m in (await dossier_service.get_dossier(db, owner, did))["pricing"]["sheets"][0]["markets"]
              if m["market"] == "DE")
    assert (de["status"], de["status_text"], de["option"]) == ("missing_config", "费用规则未配齐", None)
    assert de["reasons"]


async def test_link_only_own_sheet_and_unlink(db):
    owner, other = await _user(db, "a"), await _user(db, "b")
    did = await _dossier(db, owner)
    sheet = await sheet_service.create_sheet(db, owner, SheetCreate(product_name="P"))
    with pytest.raises(SheetNotFoundError):
        await dossier_service.link_pricing_sheet(db, other, did, sheet.id)
    await dossier_service.link_pricing_sheet(db, owner, did, sheet.id)
    linkable = await dossier_service.my_linkable_pricing_sheets(db, owner)
    assert {"id": sheet.id, "dossier_id": did} == {k: linkable["items"][0][k] for k in ("id", "dossier_id")}
    assert (await dossier_service.my_linkable_pricing_sheets(db, other))["items"] == []
    with pytest.raises(SheetNotFoundError):
        await dossier_service.unlink_pricing_sheet(db, other, did, sheet.id)
    await dossier_service.unlink_pricing_sheet(db, owner, did, sheet.id)
    assert (await dossier_service.get_dossier(db, owner, did))["pricing"] == {"state": "none", "sheets": []}
    # 删除核价单不影响档案（ON DELETE SET NULL）
    await dossier_service.link_pricing_sheet(db, owner, did, sheet.id)
    await sheet_service.delete_sheet(db, owner, sheet.id)
    assert (await dossier_service.get_dossier(db, owner, did))["pricing"]["state"] == "none"


async def test_create_draft_from_dossier(db):
    owner = await _user(db, "a")
    did = await _dossier(db, owner, title="火鸡腌制袋")
    sid = (await dossier_service.create_pricing_sheet(db, owner, did))["sheet_id"]
    s = await sheet_service.get_sheet(db, owner, sid)
    assert (s.product_name, s.status, s.draft_input, s.markets) == (
        "火鸡腌制袋", "draft", {"mode": "target_profit", "markets": ["US"]}, ["US"])
    p = (await dossier_service.get_dossier(db, owner, did))["pricing"]
    assert p["state"] == "draft_only" and p["sheets"][0]["id"] == sid


async def test_decision_freezes_pricing_status(db):
    owner = await _user(db, "a")
    await _seed_us(db, owner)
    did = await _dossier(db, owner)
    # 没有关联核价单也能签，记录为 none
    await dossier_service.add_decision(db, owner, did, DecisionCreate(
        verdict="reject", reason="先不做", client_token=uuid.uuid4().hex))
    assert (await dossier_service.get_dossier(db, owner, did))["decisions"][0]["pricing_status"] == {
        "state": "none", "sheets": []}

    sheet = await sheet_service.create_sheet(db, owner, SheetCreate(product_name="P"))
    await dossier_service.link_pricing_sheet(db, owner, did, sheet.id)
    await _save(db, owner, sheet.id)
    await dossier_service.add_decision(db, owner, did, DecisionCreate(
        verdict="watch", reason="利润够，等复查", recheck_date=sheet_service.today(), client_token=uuid.uuid4().hex))
    frozen = (await dossier_service.get_dossier(db, owner, did))["decisions"][0]["pricing_status"]
    us = frozen["sheets"][0]["markets"][0]
    assert (frozen["state"], us["market"], us["label"], us["price"], us["profit_cny"], us["selected"]) == (
        "calculated", "US", "测试 专线", "93.75", "40.00", False)

    # 之后改了目标利润并保存新版本：已签的决定不变
    await _save(db, owner, sheet.id, target_profit="80")
    detail = await dossier_service.get_dossier(db, owner, did)
    assert detail["decisions"][0]["pricing_status"] == frozen
    assert _us(detail["pricing"])["option"]["price"] != "93.75"


async def test_ask_context_includes_pricing_and_prompt_forbids_computing(db):
    owner = await _user(db, "a")
    await _seed_us(db, owner)
    did = await _dossier(db, owner)
    sheet = await sheet_service.create_sheet(db, owner, SheetCreate(product_name="直发梳 核价"))
    await dossier_service.link_pricing_sheet(db, owner, did, sheet.id)
    await _save(db, owner, sheet.id)
    from app.table.opportunity.dossier_model import OppDossier

    d = await db.get(OppDossier, did)
    ctx, _ = await dossier_ask.build_context(db, d, 2_000_000_000)
    assert ctx["pricing"]["state"] == "已有测算"
    us = ctx["pricing"]["sheets"][0]["markets"][0]
    assert (us["market"], us["result_status"], us["option"]["price"], us["option"]["profit_cny"]) == (
        "US", "可计算", "93.75", "40.00")
    # 采购成本是创建人的私有输入，不进追问证据（采购价上限 max_purchase_cost 是引擎结果，可以进）
    assert '"purchase_cost"' not in json.dumps(ctx, default=str)
    assert "不得自行计算、换算或估算" in dossier_ask.SYSTEM_PROMPT
    # 私有输入不展示，但要告诉模型它们已计入，避免把「没展示」说成「缺数据」
    assert any("已全部计入引擎结果" in n for n in ctx["pricing"]["notes"])


def test_create_pricing_sheet_route_requires_pricing_view():
    from fastapi.routing import APIRoute

    from app.api_router import api_router
    from app.auth.permissions import OPPORTUNITY_VIEW, PRICING_VIEW

    def perms(dependant) -> set[str]:
        out: set[str] = set()
        for dep in dependant.dependencies:
            call = dep.call
            if getattr(call, "__qualname__", "").endswith("require_permission.<locals>._require"):
                out.update(c.cell_contents for c in call.__closure__ or () if isinstance(c.cell_contents, str))
            out |= perms(dep)
        return out

    routes = {(m, r.path): perms(r.dependant) for r in api_router.routes if isinstance(r, APIRoute)
              for m in r.methods if "pricing-sheets" in r.path}
    assert routes[("POST", "/api/opportunity/dossiers/{dossier_id}/pricing-sheets")] == {OPPORTUNITY_VIEW, PRICING_VIEW}
    # 关联、解除、列出只要机会中心权限（摘要是档案的一部分）
    for key, got in routes.items():
        if key[0] != "POST":
            assert got == {OPPORTUNITY_VIEW}, key
