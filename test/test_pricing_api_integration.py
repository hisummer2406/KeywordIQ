"""定价核算的服务层集成测试（真库）。默认跳过，只在设置 TEST_DATABASE_URL 时运行。

钉住的事（specs/pricing-rate-management 与 pricing-records）：
- 规则：载荷严格校验、启用互斥（重叠 409）、已启用不可改、停用保留历史、按核价日期唯一命中；
- 核价单：他人记录 404（PRICE-09）、保存时服务端重算、client_token 幂等、草稿乐观锁 409（PRICE-14）、
  费率更新不改历史金额且提示重算（PRICE-08）、只能选定可用方案、导出与快照一致（PRICE-15）。
"""

from __future__ import annotations

import os
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.table.pricing import rule_service, sheet_service
from app.table.pricing.pricing_errors import (
    OptionNotSelectableError,
    RuleConflictError,
    RuleImmutableError,
    RuleInvalidError,
    RuleOverlapError,
    SheetConflictError,
    SheetNotFoundError,
    VersionNotCurrentError,
)
from app.table.pricing.pricing_model import PricingSelectionLog
from app.table.pricing.pricing_schemas import (
    PricingInput,
    RuleCreate,
    RuleUpdate,
    SaveVersionRequest,
    SelectRequest,
    SheetCreate,
    SheetListRequest,
    SheetUpdate,
)

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)

LONG_AGO = date(2020, 1, 1)


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture()
async def db():
    engine = create_async_engine(_async_url(TEST_URL), poolclass=NullPool)
    connection = await engine.connect()
    trans = await connection.begin()
    session = AsyncSession(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        if await session.scalar(text("SELECT to_regclass('analysis.pricing_rule')")) is None:
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
    return (await db.execute(text(
        "INSERT INTO analysis.user_center(user_name,hashed_pwd,is_active,is_super) "
        "VALUES (:n,'x',true,false) RETURNING id"), {"n": f"{name}-{uuid.uuid4().hex[:6]}"})).scalar()


async def _active(db, uid, kind, scope, payload, start=LONG_AGO, end=None):
    r = await rule_service.create_draft(db, uid, RuleCreate(kind=kind, scope_key=scope, effective_from=start,
                                                            effective_to=end, source="测试构造", payload=payload))
    return await rule_service.activate(db, uid, r.id, r.lock_version)


MP = {"price_includes_vat": False, "vat_rate": "0"}
REF = {"categories": [{"key": "general", "name": "通用", "tiers": [{"rate": "0.20"}]}]}
CH = {"site": "US", "code": "C1", "name": "专线", "carrier": "测试", "allowed_attributes": ["general"],
      "brackets": [{"max_weight_kg": "2", "per_parcel_fee": "20", "per_kg": "0"}], "duty_mode": "included"}


async def _seed_us(db, uid, fx="1"):
    await _active(db, uid, "market_params", "US", MP)
    fx_rule = await _active(db, uid, "fx_rate", "USD", {"currency": "USD", "cny_per_unit": fx})
    await _active(db, uid, "referral_schedule", "US", REF)
    await _active(db, uid, "fbm_channel", "US:C1", CH)
    return fx_rule


def _input(**kw):
    base = {"mode": "target_profit", "markets": ["US"], "purchase_cost": "10", "packaging_labor": "5",
            "weight_kg": "0.1", "category": "general", "target_profit": "40", "include_fba": False}
    base.update(kw)
    return PricingInput.model_validate(base)


def _option(result, market="US", key="FBM:C1"):
    m = next(m for m in result["markets"] if m["market"] == market)
    return next(o for o in m["options"] if o["option_key"] == key)


# ---------------------------------------------------------------- 规则


async def test_rule_payload_validation_rejects_bad_and_extra_fields(db):
    uid = await _user(db, "rule")
    with pytest.raises(RuleInvalidError) as exc:
        await rule_service.create_draft(db, uid, RuleCreate(
            kind="referral_schedule", scope_key="US", effective_from=LONG_AGO, source="x",
            payload={"categories": [{"key": "g", "name": "g", "tiers": [{"up_to": "10", "rate": "0.1"}]}]}))
    assert "categories.0" in exc.value.message and "阶梯" in exc.value.message
    with pytest.raises(RuleInvalidError):
        await rule_service.create_draft(db, uid, RuleCreate(
            kind="fx_rate", scope_key="USD", effective_from=LONG_AGO, source="x",
            payload={"currency": "USD", "cny_per_unit": "7.1", "oops": 1}))
    with pytest.raises(RuleInvalidError):  # 范围与载荷不一致
        await rule_service.create_draft(db, uid, RuleCreate(
            kind="fx_rate", scope_key="EUR", effective_from=LONG_AGO, source="x",
            payload={"currency": "USD", "cny_per_unit": "7.1"}))


async def test_rule_float_rejected(db):
    uid = await _user(db, "rule")
    with pytest.raises(RuleInvalidError):
        await rule_service.create_draft(db, uid, RuleCreate(
            kind="fx_rate", scope_key="USD", effective_from=LONG_AGO, source="x",
            payload={"currency": "USD", "cny_per_unit": 7.1}))


async def test_activate_overlap_rejected_then_allowed_after_end(db):
    uid = await _user(db, "rule")
    old = await _active(db, uid, "fx_rate", "USD", {"currency": "USD", "cny_per_unit": "7.1"})
    new = await rule_service.create_draft(db, uid, RuleCreate(
        kind="fx_rate", scope_key="USD", effective_from=date(2026, 4, 17), source="x",
        payload={"currency": "USD", "cny_per_unit": "7.2"}))
    assert new.version == old.version + 1
    with pytest.raises(RuleOverlapError):
        await rule_service.activate(db, uid, new.id, new.lock_version)
    old = await rule_service.set_active_end(db, uid, old.id, old.lock_version, date(2026, 4, 16))
    new = await rule_service.activate(db, uid, new.id, new.lock_version)
    assert new.status == "active"
    rules, _ = await rule_service.resolve(db, ["US"], date(2026, 4, 16))
    assert rules["US"].fx_rate == Decimal("7.1")
    rules, _ = await rule_service.resolve(db, ["US"], date(2026, 4, 17))
    assert rules["US"].fx_rate == Decimal("7.2")


async def test_active_rule_immutable_and_lock_conflict(db):
    uid = await _user(db, "rule")
    r = await _active(db, uid, "fx_rate", "USD", {"currency": "USD", "cny_per_unit": "7.1"})
    with pytest.raises(RuleImmutableError):
        await rule_service.update_draft(db, uid, r.id, RuleUpdate(lock_version=r.lock_version, source="改"))
    with pytest.raises(RuleImmutableError):
        await rule_service.delete_draft(db, r.id, r.lock_version)
    d = await rule_service.copy_as_draft(db, uid, r.id)
    assert d.status == "draft" and d.version == r.version + 1
    with pytest.raises(RuleConflictError):
        await rule_service.update_draft(db, uid, d.id, RuleUpdate(lock_version=d.lock_version + 5, source="y"))
    d2 = await rule_service.update_draft(db, uid, d.id, RuleUpdate(lock_version=d.lock_version, source="新来源"))
    assert d2.source == "新来源" and d2.lock_version == d.lock_version + 1
    await rule_service.delete_draft(db, d2.id, d2.lock_version)


async def test_retired_rule_not_resolved_and_kept(db):
    uid = await _user(db, "rule")
    r = await _active(db, uid, "fx_rate", "USD", {"currency": "USD", "cny_per_unit": "7.1"})
    r = await rule_service.retire(db, uid, r.id, r.lock_version, "汇率过期")
    assert r.status == "retired" and r.retired_reason == "汇率过期"
    rules, _ = await rule_service.resolve(db, ["US"], date.today())
    assert rules["US"].fx_rate is None
    assert (await rule_service.get_rule(db, r.id)).status == "retired"


# ---------------------------------------------------------------- 试算与核价单


async def test_calculate_resolves_rules_and_matches_price01(db):
    uid = await _user(db, "calc")
    await _seed_us(db, uid)
    resp = await sheet_service.calculate_only(db, _input())
    o = _option(resp.result)
    assert o["target_price"] == "93.75"
    assert o["evaluation"]["profit_cny"] == "40.00"
    kinds = sorted(r["kind"] for r in resp.rule_refs)
    assert kinds == ["fbm_channel", "fx_rate", "market_params", "referral_schedule"]


async def test_missing_config_market(db):
    uid = await _user(db, "calc")
    await _seed_us(db, uid)
    resp = await sheet_service.calculate_only(db, _input(markets=["US", "DE"]))
    de = next(m for m in resp.result["markets"] if m["market"] == "DE")
    assert de["status"] == "missing_config"


async def test_other_users_sheet_is_404(db):
    a, b = await _user(db, "a"), await _user(db, "b")
    sheet = await sheet_service.create_sheet(db, b, SheetCreate(product_name="B 的产品"))
    for call in (
        sheet_service.get_sheet(db, a, sheet.id),
        sheet_service.update_sheet(db, a, sheet.id, SheetUpdate(lock_version=sheet.lock_version, sku="x")),
        sheet_service.copy_sheet(db, a, sheet.id),
        sheet_service.delete_sheet(db, a, sheet.id),
        sheet_service.prepare_export(db, a, sheet.id, 1),
    ):
        with pytest.raises(SheetNotFoundError):
            await call
    listing = await sheet_service.list_sheets(db, a, SheetListRequest())
    assert all(i.id != sheet.id for i in listing.items)


async def test_draft_optimistic_lock(db):
    uid = await _user(db, "lock")
    s = await sheet_service.create_sheet(db, uid, SheetCreate(product_name="P", draft_input={"markets": ["US"]}))
    s1 = await sheet_service.update_sheet(db, uid, s.id, SheetUpdate(lock_version=s.lock_version, sku="A"))
    with pytest.raises(SheetConflictError):
        await sheet_service.update_sheet(db, uid, s.id, SheetUpdate(lock_version=s.lock_version, sku="B"))
    assert s1.sku == "A"


async def test_save_version_idempotent_and_server_recomputes(db):
    uid = await _user(db, "save")
    await _seed_us(db, uid)
    s = await sheet_service.create_sheet(db, uid, SheetCreate(product_name="P"))
    token = uuid.uuid4().hex
    v1 = await sheet_service.save_version(db, uid, s.id, SaveVersionRequest(
        lock_version=s.lock_version, client_token=token, input=_input()))
    again = await sheet_service.save_version(db, uid, s.id, SaveVersionRequest(
        lock_version=s.lock_version, client_token=token, input=_input()))
    assert again.id == v1.id and v1.version_no == 1
    assert _option(v1.result)["target_price"] == "93.75"
    detail = await sheet_service.get_sheet(db, uid, s.id)
    assert detail.status == "saved" and detail.current_version_id == v1.id and len(detail.versions) == 1
    with pytest.raises(SheetConflictError):  # 旧 lock_version 的新令牌
        await sheet_service.save_version(db, uid, s.id, SaveVersionRequest(
            lock_version=s.lock_version, client_token=uuid.uuid4().hex, input=_input()))


async def test_rule_update_keeps_history_and_flags_recalc(db):
    uid = await _user(db, "hist")
    fx = await _seed_us(db, uid, fx="2")
    s = await sheet_service.create_sheet(db, uid, SheetCreate(product_name="P"))
    v1 = await sheet_service.save_version(db, uid, s.id, SaveVersionRequest(
        lock_version=s.lock_version, client_token=uuid.uuid4().hex, input=_input()))
    before = _option(v1.result)["target_price"]
    assert (await sheet_service.get_sheet(db, uid, s.id)).rules_updated is False

    # 新汇率版本从今天起生效：旧版本截止到昨天
    yesterday = sheet_service.today() - timedelta(days=1)
    await rule_service.set_active_end(db, uid, fx.id, fx.lock_version, yesterday)
    await _active(db, uid, "fx_rate", "USD", {"currency": "USD", "cny_per_unit": "4"}, start=sheet_service.today())

    detail = await sheet_service.get_sheet(db, uid, s.id)
    assert detail.rules_updated is True
    old = await sheet_service.get_version(db, uid, s.id, v1.id)
    assert _option(old.result)["target_price"] == before  # 历史金额不变

    v2 = await sheet_service.save_version(db, uid, s.id, SaveVersionRequest(
        lock_version=detail.lock_version, client_token=uuid.uuid4().hex, input=_input()))
    assert v2.version_no == 2 and _option(v2.result)["target_price"] != before
    assert (await sheet_service.get_sheet(db, uid, s.id)).rules_updated is False


async def test_selection_rules(db):
    uid = await _user(db, "sel")
    await _seed_us(db, uid)
    await _active(db, uid, "fbm_channel", "US:C2", {**CH, "code": "C2", "duty_mode": "not_included"})
    s = await sheet_service.create_sheet(db, uid, SheetCreate(product_name="P"))
    v1 = await sheet_service.save_version(db, uid, s.id, SaveVersionRequest(
        lock_version=s.lock_version, client_token=uuid.uuid4().hex, input=_input()))
    # C2 不包税且没填关税：待补资料，不可选
    assert _option(v1.result, key="FBM:C2")["availability"] == "needs_input"
    with pytest.raises(OptionNotSelectableError):
        await sheet_service.select_option(db, uid, s.id, "US", SelectRequest(version_id=v1.id, option_key="FBM:C2"))
    out = await sheet_service.select_option(db, uid, s.id, "us",
                                            SelectRequest(version_id=v1.id, option_key="FBM:C1", note="便宜"))
    assert [(x.market, x.option_key) for x in out.selections] == [("US", "FBM:C1")]

    detail = await sheet_service.get_sheet(db, uid, s.id)
    v2 = await sheet_service.save_version(db, uid, s.id, SaveVersionRequest(
        lock_version=detail.lock_version, client_token=uuid.uuid4().hex, input=_input()))
    with pytest.raises(VersionNotCurrentError):
        await sheet_service.select_option(db, uid, s.id, "US", SelectRequest(version_id=v1.id, option_key="FBM:C1"))
    await sheet_service.select_option(db, uid, s.id, "US", SelectRequest(version_id=v2.id, option_key="FBM:C1"))
    logs = (await db.execute(select(PricingSelectionLog).where(PricingSelectionLog.sheet_id == s.id))).scalars().all()
    assert len(logs) == 2


async def test_export_matches_snapshot(db):
    uid = await _user(db, "exp")
    await _seed_us(db, uid)
    s = await sheet_service.create_sheet(db, uid, SheetCreate(product_name="导出品"))
    v = await sheet_service.save_version(db, uid, s.id, SaveVersionRequest(
        lock_version=s.lock_version, client_token=uuid.uuid4().hex, input=_input()))
    await sheet_service.select_option(db, uid, s.id, "US", SelectRequest(version_id=v.id, option_key="FBM:C1"))
    filename, rows = await sheet_service.prepare_export(db, uid, s.id, v.id)
    assert filename.endswith("_v1.csv")
    main = next(r for r in rows if r and r[0] == "US" and len(r) == 15)
    assert main[6] == "是" and main[7] == "93.75" and main[10] == "40.00"
    detail = [r for r in rows if len(r) == 9 and r[0] == "US"]
    total = sum(Decimal(r[3]) for r in detail)
    assert Decimal("93.75") - total == Decimal("40.00")  # 明细合计与利润一致
    chunks = [c async for c in sheet_service.iter_csv(rows)]
    assert chunks[0].startswith("﻿".encode())


async def test_list_filters(db):
    uid = await _user(db, "list")
    await sheet_service.create_sheet(db, uid, SheetCreate(product_name="蓝牙耳机", draft_input={"markets": ["US"]}))
    await sheet_service.create_sheet(db, uid, SheetCreate(product_name="水杯", sku="CUP-1",
                                                          draft_input={"markets": ["GB"]}))
    r = await sheet_service.list_sheets(db, uid, SheetListRequest(q="cup"))
    assert [i.product_name for i in r.items] == ["水杯"]
    r = await sheet_service.list_sheets(db, uid, SheetListRequest(market="us"))
    assert [i.product_name for i in r.items] == ["蓝牙耳机"]
    r = await sheet_service.list_sheets(db, uid, SheetListRequest(status="saved"))
    assert r.total == 0
