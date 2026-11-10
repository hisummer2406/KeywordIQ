"""初始费率草稿：载荷全部合法，且抽查费用与来源表格一致（change add-pricing-calculation 任务 5.3）。"""

from __future__ import annotations

import importlib.util
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from app.table.pricing import rule_schemas as rs
from app.table.pricing.engine.fba import classify, fulfillment_fee, make_package

_spec = importlib.util.spec_from_file_location(
    "pricing_seed_drafts", Path(__file__).resolve().parent.parent / "scripts" / "pricing_seed_drafts.py")
seed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed)


def _card(site: str, on: date):
    for d in seed.all_drafts():
        if d["kind"] != "fba_rate_card" or d["scope_key"] != site:
            continue
        if d["effective_from"] <= on and (d.get("effective_to") is None or on <= d["effective_to"]):
            return rs.to_fba(rs.parse_payload("fba_rate_card", d["payload"]), None)
    raise AssertionError("no card")


def test_all_drafts_validate():
    from app.table.pricing.rule_service import validate_payload

    drafts = seed.all_drafts()
    assert len(drafts) == 14
    for d in drafts:
        validate_payload(d["kind"], d["scope_key"], d["payload"])
        assert d["source"]


@pytest.mark.parametrize(("site", "on", "dims", "kg", "price", "tier", "fee"), [
    # GB 20×15×5 cm、0.3 kg 落在超大号信封（≤33×23×6 cm、≤960 g，按实重）：£2.94（第 6 页）
    ("GB", date(2026, 8, 1), ("20", "15", "5"), "0.3", "20", "xl_envelope", "2.94"),
    # GB 25×15×8 cm 体积重 0.6 kg > 实重 0.3 kg → 小号包裹 ≤900 g：£3.04
    ("GB", date(2026, 8, 1), ("25", "15", "8"), "0.3", "20", "small_parcel", "3.04"),
    # DE 旺季 40×30×8 cm 体积重 1.92 kg > 实重 1.5 kg → 标准包裹 ≤2.9 kg：DE Only €4.84（第 7 页）
    ("DE", date(2026, 11, 10), ("40", "30", "8"), "1.5", "30", "standard_parcel", "4.84"),
    # GB 轻型信封 ≤ 60 g：£1.89
    ("GB", date(2026, 8, 1), ("30", "20", "2"), "0.05", "10", "light_envelope", "1.89"),
    # US 非旺季小号标准 2+～4 oz、$10–50：$3.42
    ("US", date(2026, 8, 1), ("20", "15", "1.5"), "0.1", "15", "small_standard", "3.42"),
    # US 旺季大号标准：30×20×5 cm 体积重 1.317 lb > 实重 0.88 lb → 1.25+～1.5 lb、<$10：$4.91
    ("US", date(2026, 11, 10), ("30", "20", "5"), "0.4", "9.99", "large_standard", "4.91"),
])
def test_spot_check_fulfillment_fees(site, on, dims, kg, price, tier, fee):
    card = _card(site, on)
    m = classify(card, make_package(D(kg), *(D(x) for x in dims)))
    assert m.tier.key == tier
    got, _ = fulfillment_fee(card, m, D(price))
    assert got == D(fee)


def test_us_large_standard_overflow_above_3lb():
    card = _card("US", date(2026, 8, 1))
    # 4 lb ≈ 1.814 kg，体积小于实重：6.97 + 4 × 0.08（每 4 oz）= 7.29
    m = classify(card, make_package(D("1.81436948"), D("30"), D("20"), D("10")))
    assert m.tier.key == "large_standard"
    got, _ = fulfillment_fee(card, m, D("20"))
    assert got == D("7.29")
