"""核价共享规则的载荷结构与引擎对象转换（design D8）。

载荷存 JSONB，写入前按 ``kind`` 用这里的模型**严格**校验：拒绝多余字段、金额只收十进制
字符串或整数（拒绝 float，见 ``engine.money``）。数据库不校验载荷内容，这里是唯一的闸。

``payload_schema`` 目前恒为 1。载荷结构不兼容地变化时递增，并在 :func:`parse_payload`
里按版本分派——已保存快照里的旧载荷原样保留，不回写升级。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from app.table.pricing.engine import types as t
from app.table.pricing.engine.money import to_decimal

PAYLOAD_SCHEMA = 1

#: 货物属性枚举。前端选项与渠道「允许属性」共用这一份。
CARGO_ATTRIBUTES: dict[str, str] = {
    "general": "普货",
    "battery": "带电（内置电池）",
    "battery_only": "纯电池",
    "liquid": "液体",
    "paste": "膏体",
    "powder": "粉末",
    "cosmetic": "化妆品",
    "magnetic": "带磁",
    "hazmat": "危险品",
}


def _dec(value: Any) -> Decimal:
    # Pydantic 只把 ValueError 转成校验错误；TypeError 会穿透成 500，这里统一转换。
    try:
        return to_decimal(value)
    except (TypeError, ArithmeticError) as exc:
        raise ValueError(str(exc) or "不是合法的十进制数") from None


Dec = Annotated[Decimal, BeforeValidator(_dec)]
NonNeg = Annotated[Decimal, BeforeValidator(_dec), Field(ge=0)]
Rate = Annotated[Decimal, BeforeValidator(_dec), Field(ge=0, le=1)]
Pos = Annotated[Decimal, BeforeValidator(_dec), Field(gt=0)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------- 汇率


class FxRatePayload(_Strict):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    cny_per_unit: Pos
    """1 单位外币 = 多少人民币。"""


# ---------------------------------------------------------------- 市场参数


class HeadhaulPayload(_Strict):
    mode: Literal["per_kg", "per_cbm"]
    unit_price: NonNeg
    volumetric_divisor: Pos | None = None


class MarketParamsPayload(_Strict):
    price_includes_vat: bool
    vat_rate: Rate
    import_vat_recoverable: bool = True
    amazon_fee_surcharge_rate: Rate = Decimal("0")
    default_ads_rate: Rate = Decimal("0")
    default_return_rate: Rate = Decimal("0")
    default_payout_rate: Rate = Decimal("0")
    default_storage_months: NonNeg = Decimal("0")
    headhaul: HeadhaulPayload | None = None

    @model_validator(mode="after")
    def _vat(self) -> MarketParamsPayload:
        if not self.price_includes_vat and self.vat_rate != 0:
            raise ValueError("标价不含税的市场 vat_rate 必须为 0")
        return self


# ---------------------------------------------------------------- 类目佣金


class ReferralTierPayload(_Strict):
    up_to: Pos | None = None
    rate: Rate


class ReferralCategoryPayload(_Strict):
    key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=100)
    mode: Literal["whole", "portion"] = "whole"
    tiers: list[ReferralTierPayload] = Field(min_length=1)
    min_fee: NonNeg = Decimal("0")

    @model_validator(mode="after")
    def _tiers(self) -> ReferralCategoryPayload:
        uppers = [x.up_to for x in self.tiers]
        if uppers[-1] is not None or any(u is None for u in uppers[:-1]):
            raise ValueError("阶梯只有最后一档可以不设上限，且最后一档必须不设上限")
        bounded = [u for u in uppers if u is not None]
        if bounded != sorted(bounded) or len(set(bounded)) != len(bounded):
            raise ValueError("阶梯上限必须严格递增")
        return self


class ReferralSchedulePayload(_Strict):
    categories: list[ReferralCategoryPayload] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> ReferralSchedulePayload:
        keys = [c.key for c in self.categories]
        if len(set(keys)) != len(keys):
            raise ValueError("类目 key 重复")
        return self


# ---------------------------------------------------------------- FBA 费率


class FbaTierPayload(_Strict):
    key: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_]+$")
    name: str
    max_longest: Pos | None = None
    max_median: Pos | None = None
    max_shortest: Pos | None = None
    max_length_girth: Pos | None = None
    max_unit_weight: Pos | None = None
    use_dim_weight: bool = True
    storage_group: str = "standard"
    packaging_weight: NonNeg = Decimal("0")


class FbaBracketPayload(_Strict):
    max_weight: Pos
    fee: NonNeg


class FbaOverflowPayload(_Strict):
    above: Pos
    base_fee: NonNeg
    step_weight: Pos
    step_fee: NonNeg


class FbaFeeTablePayload(_Strict):
    tier: str
    price_min: NonNeg | None = None
    price_max: Pos | None = None
    brackets: list[FbaBracketPayload] = Field(min_length=1)
    overflow: FbaOverflowPayload | None = None

    @model_validator(mode="after")
    def _brackets(self) -> FbaFeeTablePayload:
        ws = [b.max_weight for b in self.brackets]
        if ws != sorted(ws) or len(set(ws)) != len(ws):
            raise ValueError("重量区间上限必须严格递增")
        if self.price_min is not None and self.price_max is not None and self.price_max <= self.price_min:
            raise ValueError("价格段上限必须大于下限")
        return self


class FbaPeakPayload(_Strict):
    start: date
    end: date
    fees: dict[str, NonNeg]


class FbaStoragePayload(_Strict):
    storage_group: str
    months: list[Annotated[int, Field(ge=1, le=12)]] = Field(min_length=1)
    fee_per_volume: NonNeg


class FbaRateCardPayload(_Strict):
    unit_system: Literal["imperial", "metric"]
    dim_divisor: Pos
    tiers: list[FbaTierPayload] = Field(min_length=1)
    fees: list[FbaFeeTablePayload] = Field(min_length=1)
    fuel_surcharge_rate: Rate = Decimal("0")
    peak_fees: list[FbaPeakPayload] = Field(default_factory=list)
    storage_volume_unit: Literal["cuft", "m3"]
    storage_rates: list[FbaStoragePayload] = Field(default_factory=list)
    placement_fees: dict[str, dict[str, NonNeg]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _refs(self) -> FbaRateCardPayload:
        keys = [x.key for x in self.tiers]
        if len(set(keys)) != len(keys):
            raise ValueError("尺寸段 key 重复")
        unknown = {f.tier for f in self.fees} - set(keys)
        if unknown:
            raise ValueError(f"配送费引用了未定义的尺寸段：{sorted(unknown)}")
        missing = set(keys) - {f.tier for f in self.fees}
        if missing:
            raise ValueError(f"尺寸段缺少配送费：{sorted(missing)}")
        groups = {x.storage_group for x in self.tiers}
        for g in groups:
            covered = {m for r in self.storage_rates if r.storage_group == g for m in r.months}
            if covered != set(range(1, 13)):
                raise ValueError(f"仓储分组 {g} 的费率没有覆盖 1～12 月")
        return self


# ---------------------------------------------------------------- 自发货渠道


class FbmBracketPayload(_Strict):
    max_weight_kg: Pos
    per_parcel_fee: NonNeg
    per_kg: NonNeg


class FbmSurchargePayload(_Strict):
    name: str
    kind: Literal["fixed", "per_kg", "rate_on_freight"]
    value: NonNeg
    when_attributes: list[str] = Field(default_factory=list)


class FbmChannelPayload(_Strict):
    site: str
    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=100)
    carrier: str = Field(default="", max_length=60)
    allowed_attributes: list[str] = Field(min_length=1)
    max_weight_kg: Pos | None = None
    max_longest_cm: Pos | None = None
    max_dims_sum_cm: Pos | None = None
    liquid_cap_ml: Pos | None = None
    liquid_cap_scope: Literal["per_bottle", "per_parcel"] = "per_bottle"
    min_weight_kg: NonNeg = Decimal("0")
    weight_step_kg: Pos = Decimal("0.001")
    volumetric_divisor: Pos | None = None
    brackets: list[FbmBracketPayload] = Field(min_length=1)
    surcharges: list[FbmSurchargePayload] = Field(default_factory=list)
    duty_mode: Literal["included", "not_included"]
    transit_days: str | None = Field(default=None, max_length=40)
    source_cells: list[str] = Field(default_factory=list)
    """原表导入时的来源单元格（如 ``云途-普货!H5``）。"""

    @model_validator(mode="after")
    def _check(self) -> FbmChannelPayload:
        bad = (set(self.allowed_attributes) | {a for s in self.surcharges for a in s.when_attributes}) - set(
            CARGO_ATTRIBUTES)
        if bad:
            raise ValueError(f"未知货物属性：{sorted(bad)}")
        ws = [b.max_weight_kg for b in self.brackets]
        if ws != sorted(ws) or len(set(ws)) != len(ws):
            raise ValueError("重量区间上限必须严格递增")
        return self


PAYLOAD_MODELS: dict[str, type[_Strict]] = {
    "fx_rate": FxRatePayload,
    "market_params": MarketParamsPayload,
    "referral_schedule": ReferralSchedulePayload,
    "fba_rate_card": FbaRateCardPayload,
    "fbm_channel": FbmChannelPayload,
}

KIND_LABELS = {
    "fx_rate": "汇率",
    "market_params": "市场参数",
    "referral_schedule": "类目佣金",
    "fba_rate_card": "FBA 费率",
    "fbm_channel": "自发货渠道",
}


def parse_payload(kind: str, payload: dict[str, Any], schema: int = PAYLOAD_SCHEMA) -> _Strict:
    if schema != PAYLOAD_SCHEMA:
        raise ValueError(f"不支持的载荷结构版本 {schema}")
    return PAYLOAD_MODELS[kind].model_validate(payload)


def dump_payload(model: _Strict) -> dict[str, Any]:
    """转成 JSONB 存储形态：Decimal 序列化成字符串，不经过 float。"""
    return model.model_dump(mode="json")


def check_scope(kind: str, scope_key: str, model: _Strict) -> str | None:
    """范围与载荷一致性；不一致返回原因。"""
    from app.site_registry import is_known_site

    if kind == "fx_rate":
        assert isinstance(model, FxRatePayload)
        return None if scope_key == model.currency else "汇率范围必须等于币种"
    if kind == "fbm_channel":
        assert isinstance(model, FbmChannelPayload)
        if scope_key != f"{model.site}:{model.code}":
            return "渠道范围必须是 站点:渠道码"
        return None if is_known_site(model.site) else "站点未登记"
    return None if is_known_site(scope_key) else "站点未登记"


# ---------------------------------------------------------------- 转引擎对象


def to_market_params(m: MarketParamsPayload, site: str, currency: str, minor: Decimal,
                     ref: t.RuleRef | None) -> t.MarketParams:
    hh = None
    if m.headhaul is not None:
        hh = t.HeadhaulDefault(m.headhaul.mode, m.headhaul.unit_price, m.headhaul.volumetric_divisor)
    return t.MarketParams(
        site=site, currency=currency, minor_unit=minor, price_includes_vat=m.price_includes_vat,
        vat_rate=m.vat_rate, import_vat_recoverable=m.import_vat_recoverable,
        amazon_fee_surcharge_rate=m.amazon_fee_surcharge_rate, default_ads_rate=m.default_ads_rate,
        default_return_rate=m.default_return_rate, default_payout_rate=m.default_payout_rate,
        default_storage_months=m.default_storage_months, headhaul=hh, ref=ref,
    )


def to_referral(m: ReferralSchedulePayload, ref: t.RuleRef | None) -> t.ReferralSchedule:
    cats = {
        c.key: t.ReferralCategory(
            key=c.key, name=c.name, mode=c.mode, min_fee=c.min_fee,
            tiers=tuple(t.ReferralTier(x.up_to, x.rate) for x in c.tiers),
        )
        for c in m.categories
    }
    return t.ReferralSchedule(categories=cats, ref=ref)


def to_fba(m: FbaRateCardPayload, ref: t.RuleRef | None) -> t.FbaRateCard:
    return t.FbaRateCard(
        unit_system=m.unit_system, dim_divisor=m.dim_divisor,
        tiers=tuple(t.FbaSizeTier(x.key, x.name, x.max_longest, x.max_median, x.max_shortest, x.max_length_girth,
                                  x.max_unit_weight, x.use_dim_weight, x.storage_group, x.packaging_weight)
                    for x in m.tiers),
        fees=tuple(t.FbaFeeTable(
            f.tier, f.price_min, f.price_max, tuple(t.FbaWeightBracket(b.max_weight, b.fee) for b in f.brackets),
            t.FbaOverflow(f.overflow.above, f.overflow.base_fee, f.overflow.step_weight, f.overflow.step_fee)
            if f.overflow else None,
        ) for f in m.fees),
        fuel_surcharge_rate=m.fuel_surcharge_rate,
        peak_fees=tuple(t.FbaPeakFee(p.start, p.end, dict(p.fees)) for p in m.peak_fees),
        storage_volume_unit=m.storage_volume_unit,
        storage_rates=tuple(t.FbaStorageRate(r.storage_group, frozenset(r.months), r.fee_per_volume)
                            for r in m.storage_rates),
        placement_fees={k: dict(v) for k, v in m.placement_fees.items()},
        ref=ref,
    )


def to_fbm(m: FbmChannelPayload, ref: t.RuleRef | None) -> t.FbmChannel:
    return t.FbmChannel(
        code=m.code, name=m.name, carrier=m.carrier, allowed_attributes=frozenset(m.allowed_attributes),
        max_weight_kg=m.max_weight_kg, max_longest_cm=m.max_longest_cm, max_dims_sum_cm=m.max_dims_sum_cm,
        liquid_cap_ml=m.liquid_cap_ml, liquid_cap_scope=m.liquid_cap_scope, min_weight_kg=m.min_weight_kg,
        weight_step_kg=m.weight_step_kg, volumetric_divisor=m.volumetric_divisor,
        brackets=tuple(t.FbmBracket(b.max_weight_kg, b.per_parcel_fee, b.per_kg) for b in m.brackets),
        surcharges=tuple(t.FbmSurcharge(s.name, s.kind, s.value, frozenset(s.when_attributes))
                         for s in m.surcharges),
        duty_mode=t.DutyMode(m.duty_mode), transit_days=m.transit_days, ref=ref,
    )
