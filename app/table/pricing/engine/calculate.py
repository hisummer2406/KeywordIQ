"""核价主流程：单一正算模型、分段求解、复核与比较（design D1–D7）。

**只有一个正算函数**（:meth:`_OptionModel.evaluate`）。目标售价、目标利润率、保本价、
采购价上限全部由它数值求解，再把取整后的结果代回它复核——原 Excel 三套公式各算各的、
正反对不上，就是这里要避免的事。

求解思路：利润是售价的分段线性函数，分段点来自佣金阶梯、最低佣金和 FBA 价格段。
每段内取两个内点确定直线、解出该段的最小可行价，所有段的候选都按最小货币单位向上
取整并代回正算复核，取复核通过的最小值。单项收费舍到分会让直线有 ±1 分的毛刺，
复核失败就逐单位上调（有上限），这正是「反推后必须正算验证」的落点。
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from decimal import Decimal

from . import fba as fba_mod
from . import fbm as fbm_mod
from .money import CENT, ONE, ZERO, ceil_to, floor_to, money, ratio
from .types import (
    MODEL_VERSION,
    Availability,
    CalcResult,
    CompetitorComparison,
    DeclaredValueBasis,
    DutyMode,
    Evaluation,
    FbmChannel,
    FeeLine,
    MarketInput,
    MarketResult,
    MarketRules,
    Mode,
    OptionResult,
    ReferralCategory,
    RuleRef,
    Scenario,
    SolveStatus,
)

#: 求解的售价上限（市场币种）。超过它仍达不到目标即判无解，不返回天价。
PRICE_CAP = Decimal("100000")
#: 取整后复核失败时最多上调/下调的最小单位数。正常只需 0～2 步（舍分毛刺）。
MAX_VERIFY_STEPS = 200
DEFAULT_PLACEMENT = "amazon_optimized"
FBA_BLOCKED_ATTRIBUTES = frozenset({"hazmat"})

EXCLUDED_FEES = (
    "低库存费",
    "超龄库存附加费",
    "移除/弃置费",
    "长期仓储以外的库存附加费",
    "企业固定费用与所得税",
)


class _MissingRate(Exception):
    """正算途中发现必需费率缺失（如某价格段/重量没有配送费）。"""


# ---------------------------------------------------------------- 佣金


def referral_fee(cat: ReferralCategory, price: Decimal) -> Decimal:
    if cat.mode == "whole":
        tier = next((t for t in cat.tiers if t.up_to is None or price <= t.up_to), cat.tiers[-1])
        fee = price * tier.rate
    else:
        fee = ZERO
        lower = ZERO
        for t in cat.tiers:
            upper = price if t.up_to is None else min(price, t.up_to)
            if upper > lower:
                fee += (upper - lower) * t.rate
            if t.up_to is None or price <= t.up_to:
                break
            lower = t.up_to
    return max(fee, cat.min_fee)


def referral_breakpoints(cat: ReferralCategory) -> list[Decimal]:
    points = [t.up_to for t in cat.tiers if t.up_to is not None]
    if cat.min_fee > 0:
        points += [cat.min_fee / t.rate for t in cat.tiers if t.rate > 0]
    return points


# ---------------------------------------------------------------- 单方案模型


@dataclass
class _OptionModel:
    """一个「市场 × 方案」除售价与采购成本外全部确定的计算模型。"""

    market: str
    key: str
    label: str
    currency: str
    fx: Decimal
    minor_unit: Decimal
    includes_vat: bool
    vat_rate: Decimal
    fee_surcharge_rate: Decimal
    referral_cat: ReferralCategory
    ads_rate: Decimal
    return_rate: Decimal
    payout_rate: Decimal
    packaging_labor: Decimal
    duty_rate: Decimal | None
    declared_fixed: Decimal | None
    """申报价值固定金额；``None`` 表示按采购成本。"""
    import_vat_cost: bool
    breakpoints: list[Decimal] = field(default_factory=list)
    rules: list[RuleRef] = field(default_factory=list)
    referral_ref: RuleRef | None = None
    # FBA
    fulfillment: Callable[[Decimal], tuple[Decimal, str]] | None = None
    fuel_rate: Decimal = ZERO
    fba_fixed: list[tuple[str, str, Decimal, str]] = field(default_factory=list)
    """价格无关的 Amazon 费用（市场币种）：(key, label, 金额, 依据)。"""
    fba_ref: RuleRef | None = None
    # 人民币固定成本：(key, label, 金额, 依据, 规则)
    cny_fixed: list[tuple[str, str, Decimal, str, RuleRef | None]] = field(default_factory=list)
    transit_days: str | None = None
    notes: list[str] = field(default_factory=list)
    """不影响可用性、但要在结果旁说明的事项（渠道包税、超出尺寸段未计仓储……）。"""

    def _cny(self, amount: Decimal) -> Decimal:
        return money(amount * self.fx)

    def evaluate(self, price: Decimal, purchase_cost: Decimal) -> Evaluation:
        lines: list[FeeLine] = []
        cur = self.currency

        def amazon(key: str, label: str, amount: Decimal, basis: str, rule: RuleRef | None) -> Decimal:
            amt = money(amount)
            lines.append(FeeLine(key, label, self._cny(amt), amt, cur, basis, rule))
            return amt

        revenue_orig = price
        revenue_cny = self._cny(price)
        vat_orig = money(price * self.vat_rate / (ONE + self.vat_rate)) if self.includes_vat else ZERO
        if self.includes_vat:
            lines.append(FeeLine("vat", "销项增值税", self._cny(vat_orig), vat_orig, cur,
                                 f"含税价 × {self.vat_rate} ÷ (1 + {self.vat_rate})"))
        net_revenue_cny = revenue_cny - self._cny(vat_orig)

        amazon_fees = ZERO
        amazon_fees += amazon("referral", "平台佣金", referral_fee(self.referral_cat, price),
                              f"{self.referral_cat.name}，按含税标价计", self.referral_ref)
        if self.fulfillment is not None:
            fee, basis = self.fulfillment(price)
            amazon_fees += amazon("fba_fulfillment", "FBA 配送费", fee, basis, self.fba_ref)
            if self.fuel_rate:
                amazon_fees += amazon("fba_fuel", "燃油物流附加", money(fee) * self.fuel_rate,
                                      f"配送费 × {self.fuel_rate}", self.fba_ref)
        for key, label, amount, basis in self.fba_fixed:
            amazon_fees += amazon(key, label, amount, basis, self.fba_ref)
        if self.fee_surcharge_rate:
            amazon("amazon_fee_surcharge", "平台费用附加", amazon_fees * self.fee_surcharge_rate,
                   f"Amazon 费用合计 × {self.fee_surcharge_rate}", None)

        for key, label, rate in (("ads", "广告预留", self.ads_rate), ("returns", "退货损耗", self.return_rate),
                                 ("payout", "结汇手续费", self.payout_rate)):
            if rate:
                amazon(key, label, revenue_orig * rate, f"含税标价 × {rate}", None)

        lines.append(FeeLine("purchase", "采购成本", money(purchase_cost)))
        if self.packaging_labor:
            lines.append(FeeLine("packaging", "包装与人工", money(self.packaging_labor)))
        landed = money(purchase_cost) + money(self.packaging_labor)
        for key, label, amount, basis, rule in self.cny_fixed:
            lines.append(FeeLine(key, label, money(amount), basis=basis, rule=rule))
            if key == "headhaul":
                landed += money(amount)

        if self.duty_rate is not None:
            declared = purchase_cost if self.declared_fixed is None else self.declared_fixed
            duty = money(declared * self.duty_rate)
            lines.append(FeeLine("duty", "进口关税", duty, basis=f"申报价值 {money(declared)} × {self.duty_rate}"))
            landed += duty
            if self.import_vat_cost:
                ivat = money((declared + duty) * self.vat_rate)
                lines.append(FeeLine("import_vat", "进口增值税（不可抵扣）", ivat,
                                     basis=f"(申报价值 + 关税) × {self.vat_rate}"))

        cost = sum((ln.amount_cny for ln in lines), ZERO)
        profit = revenue_cny - cost
        return Evaluation(
            price=price,
            revenue_cny=revenue_cny,
            net_revenue_cny=net_revenue_cny,
            lines=tuple(lines),
            profit_cny=profit,
            margin=ratio(profit, net_revenue_cny),
            roi=ratio(profit, landed),
        )


# ---------------------------------------------------------------- 求解


def _min_price(g: Callable[[Decimal], Decimal], target: Decimal, breakpoints: Iterable[Decimal],
               unit: Decimal) -> Decimal | None:
    """求使 ``g(P) >= target`` 的最小售价（按 ``unit`` 取整、正算复核）。无解返回 ``None``。"""
    pts = sorted({p for p in breakpoints if ZERO < p < PRICE_CAP} | {ZERO, PRICE_CAP})
    candidates: list[Decimal] = []
    for lo, hi in itertools.pairwise(pts):
        width = hi - lo
        p1, p2 = lo + width / 4, lo + width * 3 / 4
        g1, g2 = g(p1), g(p2)
        slope = (g2 - g1) / (p2 - p1)
        intercept = g1 - slope * p1
        if slope > 0:
            x = (target - intercept) / slope
            if x <= hi:
                candidates.append(max(x, lo))
        elif slope * lo + intercept >= target:
            candidates.append(lo)

    best: Decimal | None = None
    for c in candidates:
        p = max(ceil_to(c, unit), unit)
        for _ in range(MAX_VERIFY_STEPS):
            if best is not None and p >= best:
                break
            if g(p) >= target:
                best = p
                break
            p += unit
    if best is None:
        return None
    # 舍分毛刺可能让直线解偏高一个单位：向下确认已是最小可行价。
    for _ in range(MAX_VERIFY_STEPS):
        lower = best - unit
        if lower <= 0 or g(lower) < target:
            break
        best = lower
    return best


def _max_purchase(model: _OptionModel, price: Decimal, target_of: Callable[[Evaluation], Decimal]) -> Decimal | None:
    """固定售价下，求仍满足目标的最高采购价（向下取分并复核）。负数视为无解。"""
    e0 = model.evaluate(price, ZERO)
    e1 = model.evaluate(price, Decimal("100"))
    k = (e0.profit_cny - e1.profit_cny) / Decimal("100")
    if k <= 0:
        return None
    c = floor_to((e0.profit_cny - target_of(e0)) / k, CENT)
    for _ in range(MAX_VERIFY_STEPS):
        if c < 0:
            return None
        e = model.evaluate(price, c)
        if e.profit_cny >= target_of(e):
            break
        c -= CENT
    else:
        return None
    # 同理向上确认已是最高可接受采购价。
    for _ in range(MAX_VERIFY_STEPS):
        e = model.evaluate(price, c + CENT)
        if e.profit_cny < target_of(e):
            break
        c += CENT
    return c


# ---------------------------------------------------------------- 方案构建


@dataclass(frozen=True)
class _Blocked:
    availability: Availability
    reasons: tuple[str, ...]


def _declared(sc: Scenario) -> tuple[Decimal | None, str | None]:
    if sc.declared_basis == DeclaredValueBasis.FIXED:
        if sc.declared_value is None:
            return None, "待补资料：申报价值"
        return sc.declared_value, None
    return None, None


def _base_model(sc: Scenario, rules: MarketRules, mi: MarketInput, key: str, label: str,
                cat: ReferralCategory) -> _OptionModel:
    p = rules.params
    assert p is not None and rules.fx_rate is not None and rules.referral is not None
    model = _OptionModel(
        market=rules.site,
        key=key,
        label=label,
        currency=p.currency,
        fx=rules.fx_rate,
        minor_unit=p.minor_unit,
        includes_vat=p.price_includes_vat,
        vat_rate=p.vat_rate if p.price_includes_vat else ZERO,
        fee_surcharge_rate=p.amazon_fee_surcharge_rate,
        referral_cat=cat,
        ads_rate=mi.ads_rate if mi.ads_rate is not None else p.default_ads_rate,
        return_rate=mi.return_rate if mi.return_rate is not None else p.default_return_rate,
        payout_rate=mi.payout_rate if mi.payout_rate is not None else p.default_payout_rate,
        packaging_labor=sc.packaging_labor,
        duty_rate=None,
        declared_fixed=None,
        import_vat_cost=False,
        referral_ref=rules.referral.ref,
    )
    model.breakpoints = referral_breakpoints(cat)
    model.rules = [r for r in (p.ref, rules.fx_ref, rules.referral.ref) if r is not None]
    return model


def _build_fba(sc: Scenario, rules: MarketRules, mi: MarketInput, cat: ReferralCategory) -> _OptionModel | _Blocked:
    card = rules.fba
    p = rules.params
    assert p is not None
    if card is None:
        return _Blocked(Availability.MISSING_CONFIG, ("缺少费用配置：FBA 费率",))
    blocked = sorted(sc.attributes & FBA_BLOCKED_ATTRIBUTES)
    if blocked:
        return _Blocked(Availability.NOT_APPLICABLE, ("危险品需单独审核，FBA 费率未覆盖",))
    missing: list[str] = []
    if sc.weight_kg is None:
        missing.append("重量")
    if None in (sc.length_cm, sc.width_cm, sc.height_cm):
        missing.append("尺寸")
    if mi.duty_rate is None:
        missing.append("关税税率")
    declared_fixed, declared_missing = _declared(sc)
    if declared_missing:
        missing.append("申报价值")
    if mi.headhaul_per_unit is None and p.headhaul is None:
        missing.append("头程（市场未配置默认报价）")
    if missing:
        return _Blocked(Availability.NEEDS_INPUT, tuple(f"待补资料：{m}" for m in missing))

    pkg = fba_mod.make_package(sc.weight_kg, sc.length_cm, sc.width_cm, sc.height_cm)  # type: ignore[arg-type]
    match = fba_mod.classify(card, pkg)
    model = _base_model(sc, rules, mi, "FBA", "FBA", cat)
    model.fba_ref = card.ref
    if card.ref is not None:
        model.rules.append(card.ref)
    model.duty_rate = mi.duty_rate
    model.declared_fixed = declared_fixed
    model.import_vat_cost = p.price_includes_vat and not p.import_vat_recoverable

    notes: list[str] = []
    if mi.fba_fee_override is not None:
        override = mi.fba_fee_override
        model.fulfillment = lambda _price: (override, "人工填写")
    elif match is None:
        return _Blocked(Availability.NOT_APPLICABLE, ("超出已配置尺寸段（可手填 FBA 配送费）",))
    else:
        def _fee(price: Decimal, _m=match) -> tuple[Decimal, str]:
            got = fba_mod.fulfillment_fee(card, _m, price)
            if got is None:
                raise _MissingRate("该尺寸段/重量/价格段没有配送费")
            return got
        model.fulfillment = _fee
        model.breakpoints += fba_mod.price_breakpoints(card, match.tier.key)
    model.fuel_rate = card.fuel_surcharge_rate

    if match is None:
        notes.append("超出已配置尺寸段：仓储费、入库配置费、旺季费未计入")
    else:
        tier = match.tier.key
        peak = fba_mod.peak_fee(card, tier, sc.calc_date)
        if peak:
            model.fba_fixed.append(("fba_peak", "旺季配送附加", peak, f"{sc.calc_date} 处于旺季费期间"))
        months = mi.storage_months if mi.storage_months is not None else p.default_storage_months
        storage = fba_mod.storage_fee(card, match, pkg, sc.calc_date, months)
        if storage is None:
            return _Blocked(Availability.MISSING_CONFIG, ("缺少费用配置：仓储费率",))
        if storage:
            model.fba_fixed.append(("fba_storage", "月度仓储费", storage,
                                    f"{months} 个月，{match.tier.storage_group} 费率"))
        option = mi.placement_option or DEFAULT_PLACEMENT
        placement = fba_mod.placement_fee(card, tier, option)
        if placement is None and option != DEFAULT_PLACEMENT:
            return _Blocked(Availability.MISSING_CONFIG, (f"缺少费用配置：入库配置费（{option}）",))
        if placement:
            model.fba_fixed.append(("fba_placement", "入库配置费", placement, option))

    if mi.headhaul_per_unit is not None:
        model.cny_fixed.append(("headhaul", "头程", mi.headhaul_per_unit, "人工填写", None))
    else:
        assert p.headhaul is not None
        cost, basis = fba_mod.headhaul_cost(p.headhaul, pkg)
        model.cny_fixed.append(("headhaul", "头程", cost, basis, p.ref))
    model.notes = notes
    return model


def _build_fbm(sc: Scenario, rules: MarketRules, mi: MarketInput, cat: ReferralCategory,
               ch: FbmChannel) -> _OptionModel | _Blocked:
    status, reasons = fbm_mod.check_channel(ch, sc)
    if status == "not_applicable":
        return _Blocked(Availability.NOT_APPLICABLE, tuple(reasons))
    missing = list(reasons)
    declared_fixed, declared_missing = _declared(sc)
    if ch.duty_mode == DutyMode.NOT_INCLUDED:
        if mi.duty_rate is None:
            missing.append("待补资料：关税税率")
        if declared_missing:
            missing.append(declared_missing)
    if missing:
        return _Blocked(Availability.NEEDS_INPUT, tuple(missing))
    fr = fbm_mod.freight(ch, sc)
    if fr is None:
        return _Blocked(Availability.NOT_APPLICABLE, ("计费重超出渠道全部重量区间",))
    key = f"FBM:{ch.code}"
    model = _base_model(sc, rules, mi, key, f"{ch.carrier} {ch.name}".strip(), cat)
    if ch.ref is not None:
        model.rules.append(ch.ref)
    model.cny_fixed.append(("freight", "物流运费", fr.amount, fr.basis, ch.ref))
    for name, amount in fr.surcharges:
        model.cny_fixed.append(("freight_surcharge", name, amount, "渠道附加费", ch.ref))
    if ch.duty_mode == DutyMode.NOT_INCLUDED:
        model.duty_rate = mi.duty_rate
        model.declared_fixed = declared_fixed
    model.transit_days = ch.transit_days
    model.notes = ["渠道包税"] if ch.duty_mode == DutyMode.INCLUDED else []
    return model


# ---------------------------------------------------------------- 方案求解


def _target_fn(sc: Scenario) -> Callable[[Evaluation], Decimal] | None:
    if sc.mode == Mode.TARGET_PROFIT or (sc.mode in (Mode.FIXED_PRICE, Mode.MAX_PURCHASE)
                                         and sc.target_profit is not None):
        t = sc.target_profit
        assert t is not None
        return lambda _e: t
    if sc.target_margin is not None:
        m = sc.target_margin
        return lambda e: e.net_revenue_cny * m
    return None


def _solve_option(sc: Scenario, model: _OptionModel, mi: MarketInput) -> OptionResult:
    target_of = _target_fn(sc)
    notes = tuple(model.notes)
    base = OptionResult(market=model.market, option_key=model.key, label=model.label,
                        availability=Availability.AVAILABLE, reasons=notes, rules=tuple(model.rules),
                        transit_days=model.transit_days)
    unit = model.minor_unit
    try:
        if sc.mode == Mode.MAX_PURCHASE:
            assert mi.price is not None and target_of is not None
            cmax = _max_purchase(model, mi.price, target_of)
            if cmax is None:
                ev = model.evaluate(mi.price, ZERO)
                return replace(base, solve_status=SolveStatus.UNSOLVABLE, evaluation=ev,
                               reasons=(*notes, "当前售价无法覆盖目标与其他成本"))
            ev = model.evaluate(mi.price, cmax)
            purchase = cmax
            result = replace(base, solve_status=SolveStatus.MET, evaluation=ev, max_purchase_cost=cmax,
                             target_gap_cny=ev.profit_cny - target_of(ev))
        else:
            assert sc.purchase_cost is not None
            purchase = sc.purchase_cost

            def profit(price: Decimal) -> Decimal:
                return model.evaluate(price, purchase).profit_cny

            breakeven = _min_price(profit, ZERO, model.breakpoints, unit)
            if sc.mode == Mode.FIXED_PRICE:
                assert mi.price is not None
                ev = model.evaluate(mi.price, purchase)
                if target_of is None:
                    result = replace(base, evaluation=ev, breakeven_price=breakeven)
                else:
                    gap = ev.profit_cny - target_of(ev)
                    result = replace(base, evaluation=ev, breakeven_price=breakeven, target_gap_cny=gap,
                                     solve_status=SolveStatus.MET if gap >= 0 else SolveStatus.NOT_MET)
            else:
                assert target_of is not None

                def g(price: Decimal) -> Decimal:
                    e = model.evaluate(price, purchase)
                    return e.profit_cny - target_of(e)

                tp = _min_price(g, ZERO, model.breakpoints, unit)
                if tp is None:
                    return replace(base, solve_status=SolveStatus.UNSOLVABLE, breakeven_price=breakeven,
                                   reasons=(*notes, "在可行售价范围内无法达到目标"))
                ev = model.evaluate(tp, purchase)
                result = replace(base, solve_status=SolveStatus.MET, evaluation=ev, target_price=tp,
                                 breakeven_price=breakeven, target_gap_cny=ev.profit_cny - target_of(ev))

        adj_price = sc.price_overrides.get(f"{model.market}/{model.key}")
        if adj_price is not None:
            adj = model.evaluate(adj_price, purchase)
            if target_of is not None:
                gap = adj.profit_cny - target_of(adj)
                result = replace(result, adjusted=adj, adjusted_gap_cny=gap, adjusted_met=gap >= 0)
            else:
                result = replace(result, adjusted=adj)
        return result
    except _MissingRate as exc:
        return replace(base, availability=Availability.MISSING_CONFIG, reasons=(f"缺少费用配置：{exc}",))


# ---------------------------------------------------------------- 市场与总入口


def _market_blockers(sc: Scenario, rules: MarketRules, mi: MarketInput) -> tuple[str, tuple[str, ...]] | None:
    cfg: list[str] = []
    if rules.params is None:
        cfg.append("缺少费用配置：市场参数")
    if rules.fx_rate is None:
        cfg.append("缺少费用配置：汇率")
    if rules.referral is None:
        cfg.append("缺少费用配置：类目佣金")
    elif sc.category is not None and sc.category not in rules.referral.categories:
        cfg.append(f"缺少费用配置：类目佣金（{sc.category}）")
    if cfg:
        return "missing_config", tuple(cfg)
    need: list[str] = []
    if sc.category is None:
        need.append("类目")
    if sc.mode != Mode.MAX_PURCHASE and sc.purchase_cost is None:
        need.append("采购成本")
    if sc.mode in (Mode.FIXED_PRICE, Mode.MAX_PURCHASE) and mi.price is None:
        need.append("售价")
    if sc.mode == Mode.TARGET_PROFIT and sc.target_profit is None:
        need.append("目标利润")
    if sc.mode == Mode.TARGET_MARGIN and sc.target_margin is None:
        need.append("目标利润率")
    if sc.mode == Mode.MAX_PURCHASE and sc.target_profit is None and sc.target_margin is None:
        need.append("目标利润或利润率")
    if need:
        return "needs_input", tuple(f"待补资料：{n}" for n in need)
    return None


def _recommend(sc: Scenario, options: list[OptionResult]) -> list[OptionResult]:
    eligible = [o for o in options if o.availability == Availability.AVAILABLE and o.evaluation is not None]
    best: OptionResult | None = None
    if sc.mode in (Mode.TARGET_PROFIT, Mode.TARGET_MARGIN):
        pool = [o for o in eligible if o.solve_status == SolveStatus.MET and o.target_price is not None]
        best = min(pool, key=lambda o: o.target_price, default=None)  # type: ignore[arg-type,return-value]
    elif sc.mode == Mode.FIXED_PRICE:
        best = max(eligible, key=lambda o: o.evaluation.profit_cny, default=None)  # type: ignore[union-attr]
    else:
        pool = [o for o in eligible if o.max_purchase_cost is not None]
        best = max(pool, key=lambda o: o.max_purchase_cost, default=None)  # type: ignore[arg-type,return-value]
    return [replace(o, recommended=True) if o is best else o for o in options]


def _competitors(sc: Scenario, market: str, currency: str, includes_vat: bool,
                 options: list[OptionResult]) -> tuple[CompetitorComparison, ...]:
    rec = next((o for o in options if o.recommended and o.evaluation is not None), None)
    out: list[CompetitorComparison] = []
    for cp in sc.competitor_prices:
        if cp.market != market:
            continue
        if cp.currency != currency:
            out.append(CompetitorComparison(cp, False, "币种不同，不可直接比较", None))
        elif cp.includes_vat != includes_vat:
            out.append(CompetitorComparison(cp, False, "含税口径不同，不可直接比较", None))
        elif rec is None:
            out.append(CompetitorComparison(cp, True, "没有推荐方案", None))
        else:
            out.append(CompetitorComparison(cp, True, "", rec.evaluation.price - cp.price))  # type: ignore[union-attr]
    return tuple(out)


def calculate(sc: Scenario, rules_by_market: dict[str, MarketRules]) -> CalcResult:
    """核价总入口。市场之间互不影响；方案之间互不影响。"""
    markets: list[MarketResult] = []
    for site in sc.markets:
        rules = rules_by_market.get(site) or MarketRules(site, None, None, None, None, None)
        mi = sc.market_inputs.get(site, MarketInput())
        params = rules.params
        blockers = _market_blockers(sc, rules, mi)
        if blockers is not None:
            markets.append(MarketResult(market=site, currency=params.currency if params else "",
                                        fx_rate=rules.fx_rate,
                                        price_includes_vat=params.price_includes_vat if params else None,
                                        status=blockers[0], reasons=blockers[1], options=()))
            continue
        assert params is not None and rules.referral is not None and sc.category is not None
        cat = rules.referral.categories[sc.category]

        built: list[tuple[str, str, _OptionModel | _Blocked]] = []
        if sc.include_fba:
            built.append(("FBA", "FBA", _build_fba(sc, rules, mi, cat)))
        if sc.include_fbm:
            for ch in rules.fbm_channels:
                built.append((f"FBM:{ch.code}", f"{ch.carrier} {ch.name}".strip(),
                              _build_fbm(sc, rules, mi, cat, ch)))

        options: list[OptionResult] = []
        for key, label, m in built:
            if isinstance(m, _Blocked):
                options.append(OptionResult(market=site, option_key=key, label=label,
                                            availability=m.availability, reasons=m.reasons))
            else:
                options.append(_solve_option(sc, m, mi))
        options = _recommend(sc, options)

        if not options:
            status, reasons = "all_not_applicable", ("没有可核算的发货方案（未启用 FBA 或自发货渠道）",)
        elif all(o.availability == Availability.NOT_APPLICABLE for o in options):
            status, reasons = "all_not_applicable", ("全部方案不适用",)
        else:
            status, reasons = "ok", ()
        markets.append(MarketResult(
            market=site, currency=params.currency, fx_rate=rules.fx_rate,
            price_includes_vat=params.price_includes_vat, status=status, reasons=reasons,
            options=tuple(options),
            competitors=_competitors(sc, site, params.currency, params.price_includes_vat, options),
        ))
    return CalcResult(model_version=MODEL_VERSION, calc_date=sc.calc_date, mode=sc.mode,
                      markets=tuple(markets), excluded_fees=EXCLUDED_FEES)
