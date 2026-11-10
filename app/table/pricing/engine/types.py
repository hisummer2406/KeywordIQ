"""核价引擎的输入、规则与结果类型（change add-pricing-calculation D13）。

引擎是纯函数：不碰数据库、不依赖 FastAPI。规则由 service 按核价日期解析成这里的对象
再传进来；结果原样进快照。所有金额都是 ``Decimal``——见 :mod:`.money`。

单位约定（输入侧一律公制，费率卡可自带英制，由 :mod:`.fba` 换算）：
重量 kg、长度 cm、容量 ml、比例为小数（15% 写 ``0.15``），人民币记 ``CNY``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

#: 计算模型版本。改动任何会让同一输入算出不同金额的逻辑时递增——快照靠它判断
#: 「这条历史是不是用旧模型算的」。
MODEL_VERSION = "1"


class Mode(StrEnum):
    """核价方式。"""

    TARGET_PROFIT = "target_profit"
    """按目标单件利润额求最低售价。"""
    TARGET_MARGIN = "target_margin"
    """按目标利润率求最低售价。"""
    FIXED_PRICE = "fixed_price"
    """按给定售价正算利润。"""
    MAX_PURCHASE = "max_purchase"
    """按给定售价与目标利润额/率，倒推采购价上限。"""


class Availability(StrEnum):
    """方案能不能算。与「是否达标」相互独立。"""

    AVAILABLE = "available"
    NOT_APPLICABLE = "not_applicable"
    """渠道/尺寸段不接受这件货（停发、禁运属性、超限）。"""
    NEEDS_INPUT = "needs_input"
    """用户资料不全（尺寸、关税税率……）。"""
    MISSING_CONFIG = "missing_config"
    """共享费用配置缺失（汇率、佣金、FBA 费率……）。"""


class SolveStatus(StrEnum):
    MET = "met"
    NOT_MET = "not_met"
    UNSOLVABLE = "unsolvable"
    """目标在可行范围内无解（采购价上限为负、任何售价都达不到目标利润率）。"""
    NOT_COMPUTED = "not_computed"
    """方案不可用，未求解。"""


class DeclaredValueBasis(StrEnum):
    PURCHASE_COST = "purchase_cost"
    FIXED = "fixed"


class DutyMode(StrEnum):
    INCLUDED = "included"
    """渠道包税：报价已含进口关税。"""
    NOT_INCLUDED = "not_included"
    """渠道不包税：按申报价值 × 关税税率计。"""


# ---------------------------------------------------------------- 规则


@dataclass(frozen=True)
class RuleRef:
    """结果引用的一条规则：id、版本号，供快照复现与「规则已更新」判断。"""

    kind: str
    rule_id: int
    version: int
    scope_key: str
    source: str


@dataclass(frozen=True)
class HeadhaulDefault:
    """FBA 头程默认报价（人民币）。"""

    mode: str
    """``per_kg``：按计费重；``per_cbm``：按体积立方米。"""
    unit_price: Decimal
    volumetric_divisor: Decimal | None = None
    """``per_kg`` 时的体积重除数（cm³/kg），如 6000；空则只按实重。"""


@dataclass(frozen=True)
class MarketParams:
    site: str
    currency: str
    minor_unit: Decimal
    price_includes_vat: bool
    vat_rate: Decimal
    import_vat_recoverable: bool
    amazon_fee_surcharge_rate: Decimal
    """对 Amazon 收取的佣金与 FBA 各项费用的附加比例（如英国数字服务税 2%）。"""
    default_ads_rate: Decimal
    default_return_rate: Decimal
    default_payout_rate: Decimal
    default_storage_months: Decimal
    headhaul: HeadhaulDefault | None
    ref: RuleRef | None = None


@dataclass(frozen=True)
class ReferralTier:
    up_to: Decimal | None
    """本档上限（含），``None`` 为无上限。按含税标价（买家实付）判断。"""
    rate: Decimal


@dataclass(frozen=True)
class ReferralCategory:
    key: str
    name: str
    mode: str
    """``whole``：售价落在哪档，整价按该档费率；``portion``：各档只对落在本档的部分计费。"""
    tiers: tuple[ReferralTier, ...]
    min_fee: Decimal = Decimal("0")


@dataclass(frozen=True)
class ReferralSchedule:
    categories: dict[str, ReferralCategory]
    ref: RuleRef | None = None


@dataclass(frozen=True)
class FbaSizeTier:
    key: str
    name: str
    max_longest: Decimal | None
    max_median: Decimal | None
    max_shortest: Decimal | None
    max_length_girth: Decimal | None
    max_unit_weight: Decimal | None
    use_dim_weight: bool
    storage_group: str
    packaging_weight: Decimal = Decimal("0")


@dataclass(frozen=True)
class FbaWeightBracket:
    max_weight: Decimal
    fee: Decimal


@dataclass(frozen=True)
class FbaOverflow:
    """最高区间以上：每超出 ``step_weight`` 加收 ``step_fee``（不足一步按一步）。"""

    above: Decimal
    base_fee: Decimal
    step_weight: Decimal
    step_fee: Decimal


@dataclass(frozen=True)
class FbaFeeTable:
    tier: str
    price_min: Decimal | None
    """价格段下限（含）；``None`` 不限。"""
    price_max: Decimal | None
    """价格段上限（不含）；``None`` 不限。"""
    brackets: tuple[FbaWeightBracket, ...]
    overflow: FbaOverflow | None = None


@dataclass(frozen=True)
class FbaPeakFee:
    start: date
    end: date
    fees: dict[str, Decimal]


@dataclass(frozen=True)
class FbaStorageRate:
    storage_group: str
    months: frozenset[int]
    fee_per_volume: Decimal


@dataclass(frozen=True)
class FbaRateCard:
    unit_system: str
    """``imperial``（尺寸 in、重量 lb）或 ``metric``（cm、kg）。门槛与重量区间都用这一制。"""
    dim_divisor: Decimal
    """体积重除数：英制 in³/lb（如 139），公制 cm³/kg（如 5000）。"""
    tiers: tuple[FbaSizeTier, ...]
    fees: tuple[FbaFeeTable, ...]
    fuel_surcharge_rate: Decimal
    peak_fees: tuple[FbaPeakFee, ...]
    storage_volume_unit: str
    """``cuft`` 或 ``m3``。"""
    storage_rates: tuple[FbaStorageRate, ...]
    placement_fees: dict[str, dict[str, Decimal]]
    """配置方式 → 尺寸段 → 单件费用。"""
    ref: RuleRef | None = None


@dataclass(frozen=True)
class FbmBracket:
    max_weight_kg: Decimal
    per_parcel_fee: Decimal
    per_kg: Decimal


@dataclass(frozen=True)
class FbmSurcharge:
    name: str
    kind: str
    """``fixed``（元/票）、``per_kg``（元/计费 kg）、``rate_on_freight``（运费比例）。"""
    value: Decimal
    when_attributes: frozenset[str] = frozenset()
    """非空时，货物含其中任一属性才收取。"""


@dataclass(frozen=True)
class FbmChannel:
    code: str
    name: str
    carrier: str
    allowed_attributes: frozenset[str]
    max_weight_kg: Decimal | None
    max_longest_cm: Decimal | None
    max_dims_sum_cm: Decimal | None
    liquid_cap_ml: Decimal | None
    liquid_cap_scope: str
    """``per_bottle`` 或 ``per_parcel``。"""
    min_weight_kg: Decimal
    weight_step_kg: Decimal
    volumetric_divisor: Decimal | None
    brackets: tuple[FbmBracket, ...]
    surcharges: tuple[FbmSurcharge, ...]
    duty_mode: DutyMode
    transit_days: str | None = None
    ref: RuleRef | None = None


@dataclass(frozen=True)
class MarketRules:
    """一个市场在核价日期命中的全部规则。缺失的为 ``None``，引擎据此报缺少配置。"""

    site: str
    params: MarketParams | None
    fx_rate: Decimal | None
    """1 单位市场币种 = 多少人民币。"""
    fx_ref: RuleRef | None
    referral: ReferralSchedule | None
    fba: FbaRateCard | None
    fbm_channels: tuple[FbmChannel, ...] = ()


# ---------------------------------------------------------------- 场景输入


@dataclass(frozen=True)
class CompetitorPrice:
    market: str
    price: Decimal
    currency: str
    includes_vat: bool
    note: str = ""


@dataclass(frozen=True)
class MarketInput:
    """按市场填写/覆盖的项。``None`` 表示没填，取市场默认值（有默认的）或报待补资料。"""

    price: Decimal | None = None
    """固定售价模式与倒推采购价模式的售价（市场币种，按前台标价口径）。"""
    duty_rate: Decimal | None = None
    ads_rate: Decimal | None = None
    return_rate: Decimal | None = None
    payout_rate: Decimal | None = None
    storage_months: Decimal | None = None
    headhaul_per_unit: Decimal | None = None
    """FBA 头程人民币/件，覆盖市场默认报价。"""
    fba_fee_override: Decimal | None = None
    """手填 FBA 配送费（市场币种），来源记为「人工填写」。"""
    placement_option: str | None = None


@dataclass(frozen=True)
class Scenario:
    mode: Mode
    calc_date: date
    markets: tuple[str, ...]
    purchase_cost: Decimal | None
    packaging_labor: Decimal = Decimal("0")
    weight_kg: Decimal | None = None
    length_cm: Decimal | None = None
    width_cm: Decimal | None = None
    height_cm: Decimal | None = None
    attributes: frozenset[str] = frozenset({"general"})
    liquid_ml: Decimal | None = None
    """液体/膏体单瓶容量。"""
    bottles: int = 1
    category: str | None = None
    target_profit: Decimal | None = None
    target_margin: Decimal | None = None
    """``MAX_PURCHASE`` 模式两者二选一；目标定价模式与 ``mode`` 对应。"""
    declared_basis: DeclaredValueBasis = DeclaredValueBasis.PURCHASE_COST
    declared_value: Decimal | None = None
    """``FIXED`` 时的申报价值（人民币）。"""
    include_fba: bool = True
    include_fbm: bool = True
    market_inputs: dict[str, MarketInput] = field(default_factory=dict)
    price_overrides: dict[str, Decimal] = field(default_factory=dict)
    """``<市场>/<方案键>`` → 用户调整后的拟售价，按它再正算一次。"""
    competitor_prices: tuple[CompetitorPrice, ...] = ()


# ---------------------------------------------------------------- 结果


@dataclass(frozen=True)
class FeeLine:
    key: str
    label: str
    amount_cny: Decimal
    amount_orig: Decimal | None = None
    currency: str = "CNY"
    basis: str = ""
    """计费依据的可读说明，如「16 + 0.1 kg × 40 元/kg」。"""
    rule: RuleRef | None = None


@dataclass(frozen=True)
class Evaluation:
    """一次正算的完整结果。"""

    price: Decimal
    revenue_cny: Decimal
    net_revenue_cny: Decimal
    """不含增值税的销售收入折人民币，利润率的分母。"""
    lines: tuple[FeeLine, ...]
    profit_cny: Decimal
    margin: Decimal | None
    roi: Decimal | None


@dataclass(frozen=True)
class OptionResult:
    market: str
    option_key: str
    """``FBA`` 或 ``FBM:<渠道码>``。"""
    label: str
    availability: Availability
    reasons: tuple[str, ...] = ()
    solve_status: SolveStatus = SolveStatus.NOT_COMPUTED
    evaluation: Evaluation | None = None
    """目标定价模式下是目标售价处的正算；固定售价/倒推模式下是给定售价处的正算。"""
    target_price: Decimal | None = None
    breakeven_price: Decimal | None = None
    max_purchase_cost: Decimal | None = None
    target_gap_cny: Decimal | None = None
    adjusted: Evaluation | None = None
    adjusted_met: bool | None = None
    adjusted_gap_cny: Decimal | None = None
    recommended: bool = False
    rules: tuple[RuleRef, ...] = ()
    transit_days: str | None = None


@dataclass(frozen=True)
class CompetitorComparison:
    price: CompetitorPrice
    comparable: bool
    reason: str
    gap: Decimal | None
    """推荐方案价格 − 竞品价格（市场币种），仅可比时给出。"""


@dataclass(frozen=True)
class MarketResult:
    market: str
    currency: str
    fx_rate: Decimal | None
    price_includes_vat: bool | None
    status: str
    """``ok`` / ``all_not_applicable`` / ``missing_config`` / ``needs_input``。"""
    reasons: tuple[str, ...]
    options: tuple[OptionResult, ...]
    competitors: tuple[CompetitorComparison, ...] = ()


@dataclass(frozen=True)
class CalcResult:
    model_version: str
    calc_date: date
    mode: Mode
    markets: tuple[MarketResult, ...]
    excluded_fees: tuple[str, ...]
    """未纳入计算的可选费用，在结果旁列出。"""
