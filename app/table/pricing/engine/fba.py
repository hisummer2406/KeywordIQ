"""FBA 尺寸分段、配送费、仓储费、入库配置费、头程（design D6）。

费率卡可以是英制（US：in / lb）或公制（EU：cm / kg）。输入一律公制，这里换算。
尺寸门槛按三边从长到短排序后比较；「长度＋周长」= 最长边 + 2 ×（次长 + 最短）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .money import ZERO, ceil_to
from .types import FbaRateCard, FbaSizeTier, HeadhaulDefault

CM_PER_IN = Decimal("2.54")
KG_PER_LB = Decimal("0.45359237")
CM3_PER_CUFT = Decimal("28316.846592")
CM3_PER_M3 = Decimal("1000000")


@dataclass(frozen=True)
class Package:
    weight_kg: Decimal
    dims_cm: tuple[Decimal, Decimal, Decimal]
    """从长到短排序。"""

    @property
    def volume_cm3(self) -> Decimal:
        a, b, c = self.dims_cm
        return a * b * c


def make_package(weight_kg: Decimal, l: Decimal, w: Decimal, h: Decimal) -> Package:  # noqa: E741
    a, b, c = sorted((l, w, h), reverse=True)
    return Package(weight_kg=weight_kg, dims_cm=(a, b, c))


@dataclass(frozen=True)
class TierMatch:
    tier: FbaSizeTier
    unit_weight: Decimal
    dim_weight: Decimal
    shipping_weight: Decimal
    weight_unit: str


def _card_units(card: FbaRateCard, pkg: Package) -> tuple[tuple[Decimal, Decimal, Decimal], Decimal, str, str]:
    if card.unit_system == "imperial":
        dims = tuple(d / CM_PER_IN for d in pkg.dims_cm)
        return dims, pkg.weight_kg / KG_PER_LB, "lb", "in"  # type: ignore[return-value]
    return pkg.dims_cm, pkg.weight_kg, "kg", "cm"


def classify(card: FbaRateCard, pkg: Package) -> TierMatch | None:
    """按费率卡顺序返回第一个容纳该包裹的尺寸段；都容纳不下返回 ``None``。"""
    (a, b, c), weight, wunit, _ = _card_units(card, pkg)
    girth = a + 2 * (b + c)
    for tier in card.tiers:
        if tier.max_longest is not None and a > tier.max_longest:
            continue
        if tier.max_median is not None and b > tier.max_median:
            continue
        if tier.max_shortest is not None and c > tier.max_shortest:
            continue
        if tier.max_length_girth is not None and girth > tier.max_length_girth:
            continue
        if tier.max_unit_weight is not None and weight > tier.max_unit_weight:
            continue
        dim_weight = a * b * c / card.dim_divisor
        ship = (max(weight, dim_weight) if tier.use_dim_weight else weight) + tier.packaging_weight
        return TierMatch(tier=tier, unit_weight=weight, dim_weight=dim_weight, shipping_weight=ship,
                         weight_unit=wunit)
    return None


def price_breakpoints(card: FbaRateCard, tier_key: str) -> list[Decimal]:
    """该尺寸段配送费随售价跳变的位置（价格段边界），供求解器划分线性段。"""
    points: set[Decimal] = set()
    for table in card.fees:
        if table.tier != tier_key:
            continue
        for p in (table.price_min, table.price_max):
            if p is not None:
                points.add(p)
    return sorted(points)


def fulfillment_fee(card: FbaRateCard, match: TierMatch, price: Decimal) -> tuple[Decimal, str] | None:
    """按尺寸段、价格段、发货重量查配送费（不含燃油附加）。查不到返回 ``None``。"""
    w = match.shipping_weight
    for table in card.fees:
        if table.tier != match.tier.key:
            continue
        if table.price_min is not None and price < table.price_min:
            continue
        if table.price_max is not None and price >= table.price_max:
            continue
        for br in table.brackets:
            if w <= br.max_weight:
                return br.fee, f"{match.tier.name}，发货重量 {w:.3f} {match.weight_unit} ≤ {br.max_weight}"
        ov = table.overflow
        if ov is not None and w > ov.above:
            steps = ceil_to((w - ov.above) / ov.step_weight, Decimal("1"))
            fee = ov.base_fee + steps * ov.step_fee
            return fee, (f"{match.tier.name}，{ov.base_fee} + 超出 {ov.above} {match.weight_unit} 部分 "
                         f"{steps} × {ov.step_fee}")
        return None
    return None


def peak_fee(card: FbaRateCard, tier_key: str, on: date) -> Decimal:
    for peak in card.peak_fees:
        if peak.start <= on <= peak.end:
            return peak.fees.get(tier_key, ZERO)
    return ZERO


def storage_fee(card: FbaRateCard, match: TierMatch, pkg: Package, start: date, months: Decimal) -> Decimal | None:
    """从核价日所在月起，按 ``months`` 个月（可含小数）累计月度仓储费。

    费率按季节（月份集合）区分；某月找不到费率返回 ``None``（缺配置），不按 0 计。
    """
    if months <= 0:
        return ZERO
    divisor = CM3_PER_CUFT if card.storage_volume_unit == "cuft" else CM3_PER_M3
    volume = pkg.volume_cm3 / divisor
    total = ZERO
    remaining = months
    year, month = start.year, start.month
    while remaining > 0:
        portion = min(remaining, Decimal("1"))
        rate = next(
            (r.fee_per_volume for r in card.storage_rates
             if r.storage_group == match.tier.storage_group and month in r.months),
            None,
        )
        if rate is None:
            return None
        total += volume * rate * portion
        remaining -= portion
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return total


def placement_fee(card: FbaRateCard, tier_key: str, option: str) -> Decimal | None:
    table = card.placement_fees.get(option)
    if table is None:
        return None
    return table.get(tier_key)


def headhaul_cost(default: HeadhaulDefault, pkg: Package) -> tuple[Decimal, str]:
    if default.mode == "per_cbm":
        cbm = pkg.volume_cm3 / CM3_PER_M3
        return cbm * default.unit_price, f"{cbm:.6f} m³ × {default.unit_price} 元/m³"
    weight = pkg.weight_kg
    if default.volumetric_divisor:
        weight = max(weight, pkg.volume_cm3 / default.volumetric_divisor)
    return weight * default.unit_price, f"计费重 {weight:.3f} kg × {default.unit_price} 元/kg"
