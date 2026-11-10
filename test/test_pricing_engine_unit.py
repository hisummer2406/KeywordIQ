"""核价引擎单元测试（change add-pricing-calculation，Req「单一正算模型」「反推与保本价」等）。

PRICE-xx 编号对应 docs/pricing/01-product-prd.md §9 与 specs/pricing-calculation 的场景。
所有参数都是人工构造的算术样例，不是任何市场的真实费率。
"""

from __future__ import annotations

import random
from datetime import date
from decimal import Decimal as D

import pytest

from app.table.pricing.engine import calculate
from app.table.pricing.engine.calculate import _min_price  # noqa: PLC2701 - 求解器性质测试
from app.table.pricing.engine.fba import classify, make_package
from app.table.pricing.engine.fbm import billable_weight
from app.table.pricing.engine.money import to_decimal
from app.table.pricing.engine.types import (
    Availability,
    CompetitorPrice,
    DutyMode,
    FbaFeeTable,
    FbaRateCard,
    FbaSizeTier,
    FbaStorageRate,
    FbaWeightBracket,
    FbmBracket,
    FbmChannel,
    HeadhaulDefault,
    MarketInput,
    MarketParams,
    MarketRules,
    Mode,
    ReferralCategory,
    ReferralSchedule,
    ReferralTier,
    Scenario,
    SolveStatus,
)

TODAY = date(2026, 11, 10)


def params(site="XX", *, vat=None, surcharge="0", headhaul=None, ads="0", ret="0", payout="0",
           import_vat_recoverable=True):
    return MarketParams(
        site=site, currency="USD", minor_unit=D("0.01"),
        price_includes_vat=vat is not None, vat_rate=D(vat or "0"),
        import_vat_recoverable=import_vat_recoverable,
        amazon_fee_surcharge_rate=D(surcharge),
        default_ads_rate=D(ads), default_return_rate=D(ret), default_payout_rate=D(payout),
        default_storage_months=D("0"), headhaul=headhaul,
    )


def referral(rate="0.20", *, mode="whole", tiers=None, min_fee="0"):
    cat = ReferralCategory(
        key="general", name="通用", mode=mode,
        tiers=tuple(tiers) if tiers else (ReferralTier(None, D(rate)),), min_fee=D(min_fee),
    )
    return ReferralSchedule(categories={"general": cat})


def channel(code="C1", *, per_parcel="20", per_kg="0", duty=DutyMode.INCLUDED, attrs=("general",),
            divisor=None, step="0.001", min_w="0", liquid_cap=None, scope="per_bottle", max_w="30"):
    return FbmChannel(
        code=code, name=code, carrier="测试", allowed_attributes=frozenset(attrs),
        max_weight_kg=D(max_w), max_longest_cm=None, max_dims_sum_cm=None,
        liquid_cap_ml=D(liquid_cap) if liquid_cap else None, liquid_cap_scope=scope,
        min_weight_kg=D(min_w), weight_step_kg=D(step),
        volumetric_divisor=D(divisor) if divisor else None,
        brackets=(FbmBracket(D(max_w), D(per_parcel), D(per_kg)),), surcharges=(), duty_mode=duty,
    )


def rules(site="XX", *, p=None, fx="1", ref=None, fba=None, channels=None):
    return MarketRules(site=site, params=p or params(site), fx_rate=D(fx) if fx else None, fx_ref=None,
                       referral=ref or referral(), fba=fba, fbm_channels=tuple(channels or (channel(),)))


def scenario(mode=Mode.TARGET_PROFIT, *, markets=("XX",), purchase="10", packaging="5", weight="0.1",
             dims=(None, None, None), include_fba=False, **kw):
    base = dict(mode=mode, calc_date=TODAY, markets=markets,
                purchase_cost=D(purchase) if purchase is not None else None, packaging_labor=D(packaging),
                weight_kg=D(weight) if weight else None, length_cm=dims[0], width_cm=dims[1], height_cm=dims[2],
                category="general", include_fba=include_fba)
    base.update(kw)
    return Scenario(**base)


def only_option(result, market="XX"):
    m = next(m for m in result.markets if m.market == market)
    assert len(m.options) >= 1, m
    return m.options[0]


# ---------------------------------------------------------------- PRICE-01～04


def test_price01_target_profit_and_breakeven():
    r = calculate(scenario(target_profit=D("40")), {"XX": rules()})
    o = only_option(r)
    assert o.availability == Availability.AVAILABLE
    assert o.target_price == D("93.75")
    assert o.breakeven_price == D("43.75")
    assert o.evaluation.profit_cny == D("40.00")
    assert o.solve_status == SolveStatus.MET
    assert o.recommended


def test_price02_target_margin():
    r = calculate(scenario(Mode.TARGET_MARGIN, target_margin=D("0.40")), {"XX": rules()})
    o = only_option(r)
    assert o.target_price == D("87.50")
    assert o.evaluation.profit_cny == D("35.00")
    assert o.evaluation.margin == D("0.4")


def test_price03_max_purchase():
    sc = scenario(Mode.MAX_PURCHASE, purchase=None, target_profit=D("40"),
                  market_inputs={"XX": MarketInput(price=D("100"))})
    o = only_option(calculate(sc, {"XX": rules()}))
    assert o.max_purchase_cost == D("15.00")
    assert o.evaluation.profit_cny == D("40.00")


def test_price03_negative_ceiling_is_unsolvable():
    sc = scenario(Mode.MAX_PURCHASE, purchase=None, target_profit=D("100"),
                  market_inputs={"XX": MarketInput(price=D("100"))})
    o = only_option(calculate(sc, {"XX": rules()}))
    assert o.solve_status == SolveStatus.UNSOLVABLE
    assert o.max_purchase_cost is None


def test_price04_adjusted_price_recomputed():
    sc = scenario(target_profit=D("40"), price_overrides={"XX/FBM:C1": D("92.99")})
    o = only_option(calculate(sc, {"XX": rules()}))
    assert o.adjusted.price == D("92.99")
    assert o.adjusted.profit_cny < D("40")
    assert o.adjusted_met is False
    assert o.adjusted_gap_cny == o.adjusted.profit_cny - D("40")


# ---------------------------------------------------------------- 含税市场


def test_vat_inclusive_market():
    p = params(vat="0.20")
    sc = scenario(Mode.FIXED_PRICE, purchase="0", packaging="0",
                  market_inputs={"XX": MarketInput(price=D("24.00"))})
    o = only_option(calculate(sc, {"XX": rules(p=p, ref=referral("0.15"), channels=[channel(per_parcel="0")])}))
    lines = {ln.key: ln.amount_cny for ln in o.evaluation.lines}
    assert lines["vat"] == D("4.00")
    assert lines["referral"] == D("3.60")
    assert o.evaluation.profit_cny == D("16.40")
    assert o.evaluation.net_revenue_cny == D("20.00")
    assert o.evaluation.margin == D("0.82")


def test_fee_surcharge_applies_to_amazon_fees_only():
    p = params(surcharge="0.02")
    sc = scenario(Mode.FIXED_PRICE, market_inputs={"XX": MarketInput(price=D("100"))})
    o = only_option(calculate(sc, {"XX": rules(p=p)}))
    lines = {ln.key: ln.amount_cny for ln in o.evaluation.lines}
    assert lines["amazon_fee_surcharge"] == D("0.40")  # 20 佣金 × 2%，不含运费


def test_no_target_in_fixed_price_mode_still_evaluates():
    sc = scenario(Mode.FIXED_PRICE, market_inputs={"XX": MarketInput(price=D("50"))})
    o = only_option(calculate(sc, {"XX": rules()}))
    assert o.evaluation.profit_cny == D("5.00")
    assert o.solve_status == SolveStatus.NOT_COMPUTED


def test_zero_revenue_margin_is_none():
    sc = scenario(Mode.FIXED_PRICE, market_inputs={"XX": MarketInput(price=D("0"))})
    o = only_option(calculate(sc, {"XX": rules()}))
    assert o.evaluation.margin is None


# ---------------------------------------------------------------- 关税与渠道（PRICE-05/06/07/13）


def test_price05_duty_rate_missing_needs_input():
    ch = channel(duty=DutyMode.NOT_INCLUDED)
    o = only_option(calculate(scenario(target_profit=D("40")), {"XX": rules(channels=[ch])}))
    assert o.availability == Availability.NEEDS_INPUT
    assert "待补资料：关税税率" in o.reasons
    assert o.recommended is False


def test_duty_and_nonrecoverable_import_vat():
    ch = channel(duty=DutyMode.NOT_INCLUDED)
    sc = scenario(Mode.FIXED_PRICE, market_inputs={"XX": MarketInput(price=D("100"), duty_rate=D("0.25"))})
    o = only_option(calculate(sc, {"XX": rules(channels=[ch])}))
    lines = {ln.key: ln.amount_cny for ln in o.evaluation.lines}
    assert lines["duty"] == D("2.50")  # 申报价值 = 采购成本 10
    assert "import_vat" not in lines  # 自发货不计进口增值税


def test_included_duty_channel_notes():
    o = only_option(calculate(scenario(target_profit=D("40")), {"XX": rules()}))
    assert "渠道包税" in o.reasons


def test_price06_blocked_attribute_and_liquid_cap():
    chs = [channel("A", attrs=("general",)), channel("B", attrs=("general", "liquid"), liquid_cap="100")]
    sc = scenario(target_profit=D("40"), attributes=frozenset({"general", "liquid"}), liquid_ml=D("150"))
    r = calculate(sc, {"XX": rules(channels=chs)})
    a, b = r.markets[0].options
    assert a.availability == b.availability == Availability.NOT_APPLICABLE
    assert any("liquid" in x for x in a.reasons)
    assert any("150" in x and "100" in x for x in b.reasons)
    assert r.markets[0].status == "all_not_applicable"


def test_price07_missing_dims_only_blocks_volumetric_channel():
    chs = [channel("A"), channel("B", divisor="6000")]
    r = calculate(scenario(target_profit=D("40")), {"XX": rules(channels=chs)})
    a, b = r.markets[0].options
    assert a.availability == Availability.AVAILABLE and a.target_price is not None
    assert b.availability == Availability.NEEDS_INPUT and "待补资料：尺寸" in b.reasons


@pytest.mark.parametrize(("weight", "expected"), [("0.05", "0.5"), ("0.5", "0.5"), ("0.501", "1.0")])
def test_price13_billable_weight_rounding(weight, expected):
    ch = channel(step="0.5", min_w="0.1")
    assert billable_weight(ch, scenario(weight=weight)) == D(expected)


def test_freight_basis_text():
    ch = channel(per_parcel="16", per_kg="40")
    sc = scenario(Mode.FIXED_PRICE, market_inputs={"XX": MarketInput(price=D("100"))})
    o = only_option(calculate(sc, {"XX": rules(channels=[ch])}))
    freight = next(ln for ln in o.evaluation.lines if ln.key == "freight")
    assert freight.amount_cny == D("20.00")
    assert "16" in freight.basis and "0.100 kg" in freight.basis.replace("0.1 kg", "0.100 kg")


# ---------------------------------------------------------------- FBA


def fba_card(*, bands=None, fuel="0"):
    tiers = (
        FbaSizeTier("small", "小号标准", D("38"), D("27"), D("2"), None, D("0.46"), False, "standard"),
        FbaSizeTier("large", "大号标准", D("45"), D("34"), D("26"), None, D("12"), True, "standard"),
    )
    if bands is None:
        fees = (
            FbaFeeTable("small", None, None, (FbaWeightBracket(D("0.46"), D("2")),)),
            FbaFeeTable("large", None, None, (FbaWeightBracket(D("12"), D("5")),)),
        )
    else:
        fees = bands
    return FbaRateCard(
        unit_system="metric", dim_divisor=D("5000"), tiers=tiers, fees=fees, fuel_surcharge_rate=D(fuel),
        peak_fees=(), storage_volume_unit="m3",
        storage_rates=(FbaStorageRate("standard", frozenset(range(1, 13)), D("0")),), placement_fees={},
    )


def fba_scenario(mode=Mode.TARGET_PROFIT, **kw):
    mi = kw.pop("mi", MarketInput(duty_rate=D("0"), headhaul_per_unit=D("0")))
    return scenario(mode, include_fba=True, include_fbm=False, purchase="0", packaging="0", weight="0.2",
                    dims=(D("20"), D("10"), D("1")), market_inputs={"XX": mi}, **kw)


def test_fba_dim_weight_used_for_large_tier():
    pkg = make_package(D("1"), D("40"), D("30"), D("20"))
    m = classify(fba_card(), pkg)
    assert m.tier.key == "large"
    assert m.dim_weight == D("4.8")
    assert m.shipping_weight == D("4.8")


def test_fba_dim_weight_cap_moves_to_next_tier():
    small = FbaSizeTier("small_parcel", "小号包裹", D("35"), D("25"), D("12"), None, D("3.9"), True, "standard",
                        max_dim_weight=D("2.10"))
    std = FbaSizeTier("std_parcel", "标准包裹", D("45"), D("34"), D("26"), None, D("11.9"), True, "standard")
    card = FbaRateCard("metric", D("5000"), (small, std), (), D("0"), (), "m3", (), {})
    # 35×25×12 = 10500 cm³ → 体积重 2.1 kg：恰好在上限内
    assert classify(card, make_package(D("0.5"), D("35"), D("25"), D("12"))).tier.key == "small_parcel"
    # 35×25×12.5 不在小号包裹尺寸内；34×25×12.4 体积重 2.108 > 2.10 → 落到标准包裹
    assert classify(card, make_package(D("0.5"), D("34"), D("25"), D("12.4"))).tier.key == "std_parcel"


def test_fba_oversize_not_applicable_without_override():
    sc = fba_scenario(target_profit=D("10"))
    sc = Scenario(**{**sc.__dict__, "length_cm": D("200")})
    o = only_option(calculate(sc, {"XX": rules(fba=fba_card(), channels=[])}))
    assert o.availability == Availability.NOT_APPLICABLE


def test_fba_override_fee_used():
    mi = MarketInput(duty_rate=D("0"), headhaul_per_unit=D("0"), fba_fee_override=D("7"))
    sc = fba_scenario(Mode.FIXED_PRICE, mi=MarketInput(**{**mi.__dict__, "price": D("100")}))
    sc = Scenario(**{**sc.__dict__, "length_cm": D("200")})
    o = only_option(calculate(sc, {"XX": rules(fba=fba_card(), channels=[])}))
    fee = next(ln for ln in o.evaluation.lines if ln.key == "fba_fulfillment")
    assert fee.amount_orig == D("7") and fee.basis == "人工填写"


def test_fba_missing_duty_and_headhaul():
    sc = fba_scenario(target_profit=D("10"), mi=MarketInput())
    o = only_option(calculate(sc, {"XX": rules(fba=fba_card(), channels=[])}))
    assert o.availability == Availability.NEEDS_INPUT
    assert "待补资料：关税税率" in o.reasons
    assert any("头程" in x for x in o.reasons)


def test_fba_headhaul_default_per_kg():
    p = params(headhaul=HeadhaulDefault("per_kg", D("10"), D("6000")))
    sc = fba_scenario(Mode.FIXED_PRICE, mi=MarketInput(duty_rate=D("0"), price=D("50")))
    o = only_option(calculate(sc, {"XX": rules(p=p, fba=fba_card(), channels=[])}))
    hh = next(ln for ln in o.evaluation.lines if ln.key == "headhaul")
    assert hh.amount_cny == D("2.00")  # 实重 0.2 > 体积重 200/6000


def test_price_band_jump_solution_moves_to_next_band():
    # 售价 <10 配送费 2，≥10 配送费 5；佣金 0。目标 8：低价段解 10 落在段外，正确答案 13.00
    bands = (
        FbaFeeTable("small", None, D("10"), (FbaWeightBracket(D("0.46"), D("2")),)),
        FbaFeeTable("small", D("10"), None, (FbaWeightBracket(D("0.46"), D("5")),)),
    )
    r = rules(fba=fba_card(bands=bands), ref=referral("0"), channels=[])
    o = only_option(calculate(fba_scenario(target_profit=D("8")), {"XX": r}))
    assert o.target_price == D("13.00")
    # 目标 5：低价段内可解
    o = only_option(calculate(fba_scenario(target_profit=D("5")), {"XX": r}))
    assert o.target_price == D("7.00")


def test_fba_missing_price_band_reports_missing_config():
    bands = (FbaFeeTable("small", None, D("10"), (FbaWeightBracket(D("0.46"), D("2")),)),)
    r = rules(fba=fba_card(bands=bands), ref=referral("0"), channels=[])
    o = only_option(calculate(fba_scenario(target_profit=D("50")), {"XX": r}))
    assert o.availability == Availability.MISSING_CONFIG


def test_fuel_surcharge_on_fulfillment():
    sc = fba_scenario(Mode.FIXED_PRICE, mi=MarketInput(duty_rate=D("0"), headhaul_per_unit=D("0"),
                                                         price=D("50")))
    o = only_option(calculate(sc, {"XX": rules(fba=fba_card(fuel="0.035"), channels=[])}))
    fuel = next(ln for ln in o.evaluation.lines if ln.key == "fba_fuel")
    assert fuel.amount_orig == D("0.07")


# ---------------------------------------------------------------- 佣金阶梯


def test_referral_whole_tier_and_min_fee():
    ref = referral(tiers=[ReferralTier(D("15"), D("0.05")), ReferralTier(None, D("0.17"))], min_fee="0.30")
    from app.table.pricing.engine.calculate import referral_fee
    cat = ref.categories["general"]
    assert referral_fee(cat, D("4")) == D("0.30")
    assert referral_fee(cat, D("15")) == D("0.75")
    assert referral_fee(cat, D("20")) == D("3.40")


def test_referral_portion_tier():
    from app.table.pricing.engine.calculate import referral_fee
    cat = referral(mode="portion", tiers=[ReferralTier(D("250"), D("0.20")),
                                          ReferralTier(None, D("0.05"))]).categories["general"]
    assert referral_fee(cat, D("300")) == D("52.50")


def test_target_crossing_whole_tier_jump():
    # 整价阶梯在 15 处从 5% 跳到 17%：利润在 15 之后下跌，解必须仍是最小可行价
    ref = referral(tiers=[ReferralTier(D("15"), D("0.05")), ReferralTier(None, D("0.17"))])
    sc = scenario(target_profit=D("-5.8"), purchase="0", packaging="0")
    o = only_option(calculate(sc, {"XX": rules(ref=ref)}))
    # P×0.95 − 20 ≥ −5.8 → P ≥ 14.947… → 14.95
    assert o.target_price == D("14.95")


# ---------------------------------------------------------------- 无解、多市场、竞品


def test_unreachable_margin_is_unsolvable():
    o = only_option(calculate(scenario(Mode.TARGET_MARGIN, target_margin=D("0.9")), {"XX": rules()}))
    assert o.solve_status == SolveStatus.UNSOLVABLE
    assert o.target_price is None


def test_price12_markets_independent_and_missing_fx():
    sc = scenario(target_profit=D("40"), markets=("XX", "YY"))
    r = calculate(sc, {"XX": rules(), "YY": rules("YY", fx=None)})
    xx, yy = r.markets
    assert xx.status == "ok" and xx.options[0].recommended
    assert yy.status == "missing_config" and "缺少费用配置：汇率" in yy.reasons
    assert yy.options == ()


def test_market_needs_category_and_purchase():
    sc = scenario(target_profit=D("40"), purchase=None, category=None)
    m = calculate(sc, {"XX": rules()}).markets[0]
    assert m.status == "needs_input"
    assert "待补资料：类目" in m.reasons and "待补资料：采购成本" in m.reasons


def test_recommend_cheapest_target_price_within_market():
    chs = [channel("A", per_parcel="30"), channel("B", per_parcel="20")]
    r = calculate(scenario(target_profit=D("40")), {"XX": rules(channels=chs)})
    rec = [o.option_key for o in r.markets[0].options if o.recommended]
    assert rec == ["FBM:B"]


def test_competitor_comparability():
    comps = (CompetitorPrice("XX", D("90"), "USD", False), CompetitorPrice("XX", D("90"), "EUR", False),
             CompetitorPrice("XX", D("90"), "USD", True))
    r = calculate(scenario(target_profit=D("40"), competitor_prices=comps), {"XX": rules()})
    c = r.markets[0].competitors
    assert c[0].comparable and c[0].gap == D("3.75")
    assert not c[1].comparable and not c[2].comparable


# ---------------------------------------------------------------- 定点数


def test_float_rejected():
    with pytest.raises(TypeError):
        to_decimal(0.1)
    assert to_decimal("0.1") + to_decimal("0.2") == D("0.3")


def test_decimal_exact_profit():
    sc = scenario(Mode.FIXED_PRICE, purchase="0.1", packaging="0.2",
                  market_inputs={"XX": MarketInput(price=D("0.3"))})
    o = only_option(calculate(sc, {"XX": rules(ref=referral("0"), channels=[channel(per_parcel="0")])}))
    assert o.evaluation.profit_cny == D("0.00")


# ---------------------------------------------------------------- 性质测试（任务 2.11）


@pytest.mark.parametrize("seed", range(40))
def test_solver_minimal_and_verified(seed):
    rnd = random.Random(seed)
    ref = referral(tiers=[ReferralTier(D(rnd.randint(5, 40)), D(rnd.choice(["0.05", "0.08", "0.15"]))),
                          ReferralTier(None, D(rnd.choice(["0.15", "0.17", "0.20"])))],
                   min_fee=rnd.choice(["0", "0.30"]))
    p = params(vat=rnd.choice([None, "0.19", "0.20"]), surcharge=rnd.choice(["0", "0.02"]),
               ads=rnd.choice(["0", "0.1"]), ret="0.03", payout="0.01")
    fx = D(rnd.choice(["7.1", "9.2", "7.75"]))
    ch = channel(per_parcel=str(rnd.randint(5, 30)), per_kg=str(rnd.randint(30, 90)))
    target = D(rnd.randint(1, 60))
    sc = scenario(target_profit=target, purchase=str(rnd.randint(1, 80)), weight=str(rnd.randint(50, 900) / 1000))
    r = rules(p=p, fx=str(fx), ref=ref, channels=[ch])
    o = only_option(calculate(sc, {"XX": r}))
    assert o.solve_status == SolveStatus.MET
    assert o.evaluation.profit_cny >= target

    lower = o.target_price - D("0.01")
    if lower > 0:
        sc_lower = Scenario(**{**sc.__dict__, "mode": Mode.FIXED_PRICE,
                               "market_inputs": {"XX": MarketInput(price=lower)}})
        assert only_option(calculate(sc_lower, {"XX": r})).evaluation.profit_cny < target


def test_min_price_unsolvable_when_never_reaches():
    assert _min_price(lambda p: D("-1"), D("0"), [], D("0.01")) is None
