"""自发货渠道适用性与运费（design D7）。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .money import ceil_to
from .types import FbmChannel, Scenario


@dataclass(frozen=True)
class Freight:
    billable_kg: Decimal
    amount: Decimal
    basis: str
    surcharges: tuple[tuple[str, Decimal], ...]


def check_channel(ch: FbmChannel, sc: Scenario) -> tuple[str, list[str]]:
    """返回 (``ok`` / ``not_applicable`` / ``needs_input``, 原因列表)。

    不适用与待补资料都要说出具体项：不适用写实际值与限制值，待补写缺哪项。
    """
    blocked: list[str] = []
    missing: list[str] = []

    disallowed = sorted(sc.attributes - ch.allowed_attributes)
    if disallowed:
        blocked.append(f"渠道不接受货物属性：{'、'.join(disallowed)}")

    if sc.weight_kg is None:
        missing.append("重量")
    elif ch.max_weight_kg is not None and sc.weight_kg > ch.max_weight_kg:
        blocked.append(f"重量 {sc.weight_kg} kg 超过上限 {ch.max_weight_kg} kg")

    needs_dims = ch.volumetric_divisor is not None or ch.max_longest_cm is not None or ch.max_dims_sum_cm is not None
    dims = (sc.length_cm, sc.width_cm, sc.height_cm)
    if needs_dims and any(d is None for d in dims):
        missing.append("尺寸")
    elif needs_dims:
        longest = max(dims)  # type: ignore[type-var]
        total = sum(dims)  # type: ignore[arg-type]
        if ch.max_longest_cm is not None and longest > ch.max_longest_cm:
            blocked.append(f"最长边 {longest} cm 超过上限 {ch.max_longest_cm} cm")
        if ch.max_dims_sum_cm is not None and total > ch.max_dims_sum_cm:
            blocked.append(f"三边和 {total} cm 超过上限 {ch.max_dims_sum_cm} cm")

    liquid_like = bool(sc.attributes & {"liquid", "paste"})
    if liquid_like and ch.liquid_cap_ml is not None:
        if sc.liquid_ml is None:
            missing.append("液体/膏体容量")
        else:
            amount = sc.liquid_ml if ch.liquid_cap_scope == "per_bottle" else sc.liquid_ml * sc.bottles
            scope = "单瓶" if ch.liquid_cap_scope == "per_bottle" else "整单"
            if amount > ch.liquid_cap_ml:
                blocked.append(f"{scope}容量 {amount} ml 超过上限 {ch.liquid_cap_ml} ml")

    if blocked:
        return "not_applicable", blocked
    if missing:
        return "needs_input", [f"待补资料：{m}" for m in missing]
    return "ok", []


def billable_weight(ch: FbmChannel, sc: Scenario) -> Decimal:
    assert sc.weight_kg is not None
    w = sc.weight_kg
    if ch.volumetric_divisor is not None:
        assert sc.length_cm is not None and sc.width_cm is not None and sc.height_cm is not None
        w = max(w, sc.length_cm * sc.width_cm * sc.height_cm / ch.volumetric_divisor)
    w = max(w, ch.min_weight_kg)
    return ceil_to(w, ch.weight_step_kg)


def freight(ch: FbmChannel, sc: Scenario) -> Freight | None:
    """区间运费＋附加费（人民币）。计费重超出所有区间返回 ``None``（不适用）。"""
    w = billable_weight(ch, sc)
    bracket = next((b for b in ch.brackets if w <= b.max_weight_kg), None)
    if bracket is None:
        return None
    base = bracket.per_parcel_fee + w * bracket.per_kg
    basis = f"{bracket.per_parcel_fee} + {w} kg × {bracket.per_kg} 元/kg"
    extras: list[tuple[str, Decimal]] = []
    for s in ch.surcharges:
        if s.when_attributes and not (sc.attributes & s.when_attributes):
            continue
        if s.kind == "fixed":
            extras.append((s.name, s.value))
        elif s.kind == "per_kg":
            extras.append((s.name, s.value * w))
        elif s.kind == "rate_on_freight":
            extras.append((s.name, s.value * base))
        else:  # pragma: no cover - 载荷校验已限定取值
            raise ValueError(f"未知附加费类型 {s.kind}")
    return Freight(billable_kg=w, amount=base, basis=basis, surcharges=tuple(extras))
