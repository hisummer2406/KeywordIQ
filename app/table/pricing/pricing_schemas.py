"""定价核算接口的请求/响应结构，以及核价输入到引擎场景的转换。

金额在 JSON 中一律是十进制字符串（如 ``"93.75"``），前端不得用浮点数参与计算——
结果由后端统一给出，前端只展示（design D9：前端不另写一套权威公式）。
"""

from __future__ import annotations

import dataclasses
import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.site_registry import UnknownSiteError, normalize_site_code
from app.table.pricing.engine import types as t
from app.table.pricing.rule_schemas import CARGO_ATTRIBUTES, NonNeg, Pos, Rate


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- 核价输入


class MarketInputSchema(_In):
    price: NonNeg | None = None
    duty_rate: Rate | None = None
    ads_rate: Rate | None = None
    return_rate: Rate | None = None
    payout_rate: Rate | None = None
    storage_months: NonNeg | None = None
    headhaul_per_unit: NonNeg | None = None
    fba_fee_override: NonNeg | None = None
    placement_option: str | None = Field(default=None, max_length=40)


class CompetitorPriceSchema(_In):
    market: str
    price: Pos
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    includes_vat: bool
    note: str = Field(default="", max_length=200)


class PricingInput(_In):
    """一次核价的全部输入。保存版本时原样进快照。"""

    mode: t.Mode
    markets: list[str] = Field(min_length=1, max_length=20)
    purchase_cost: NonNeg | None = None
    packaging_labor: NonNeg = Decimal("0")
    weight_kg: Pos | None = None
    length_cm: Pos | None = None
    width_cm: Pos | None = None
    height_cm: Pos | None = None
    attributes: list[str] = Field(default_factory=lambda: ["general"], min_length=1)
    liquid_ml: Pos | None = None
    bottles: int = Field(default=1, ge=1, le=1000)
    category: str | None = Field(default=None, max_length=64)
    target_profit: Decimal | None = None
    target_margin: Rate | None = None
    declared_basis: t.DeclaredValueBasis = t.DeclaredValueBasis.PURCHASE_COST
    declared_value: NonNeg | None = None
    include_fba: bool = True
    include_fbm: bool = True
    market_inputs: dict[str, MarketInputSchema] = Field(default_factory=dict)
    price_overrides: dict[str, Pos] = Field(default_factory=dict)
    competitor_prices: list[CompetitorPriceSchema] = Field(default_factory=list, max_length=50)

    @field_validator("target_profit", mode="before")
    @classmethod
    def _no_float(cls, v: Any) -> Any:
        from app.table.pricing.rule_schemas import _dec

        return None if v is None else _dec(v)

    @field_validator("markets")
    @classmethod
    def _markets(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for code in v:
            try:
                site = normalize_site_code(code)
            except UnknownSiteError as exc:
                raise ValueError("市场未登记") from exc
            if site not in out:
                out.append(site)
        return out

    @field_validator("attributes")
    @classmethod
    def _attrs(cls, v: list[str]) -> list[str]:
        bad = set(v) - set(CARGO_ATTRIBUTES)
        if bad:
            raise ValueError(f"未知货物属性：{sorted(bad)}")
        return sorted(set(v))

    @model_validator(mode="after")
    def _market_keys(self) -> PricingInput:
        for key in self.market_inputs:
            if key not in self.markets:
                raise ValueError(f"market_inputs 含未选择的市场 {key}")
        return self

    def to_scenario(self, calc_date: date) -> t.Scenario:
        return t.Scenario(
            mode=self.mode, calc_date=calc_date, markets=tuple(self.markets), purchase_cost=self.purchase_cost,
            packaging_labor=self.packaging_labor, weight_kg=self.weight_kg, length_cm=self.length_cm,
            width_cm=self.width_cm, height_cm=self.height_cm, attributes=frozenset(self.attributes),
            liquid_ml=self.liquid_ml, bottles=self.bottles, category=self.category,
            target_profit=self.target_profit, target_margin=self.target_margin,
            declared_basis=self.declared_basis, declared_value=self.declared_value,
            include_fba=self.include_fba, include_fbm=self.include_fbm,
            market_inputs={k: t.MarketInput(**v.model_dump()) for k, v in self.market_inputs.items()},
            price_overrides=dict(self.price_overrides),
            competitor_prices=tuple(t.CompetitorPrice(**c.model_dump()) for c in self.competitor_prices),
        )


def to_jsonable(obj: Any) -> Any:
    """把引擎结果（dataclass/Decimal/enum/frozenset/date）转成 JSON 形态，金额转字符串。"""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Decimal):
        return format(obj, "f")
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, date | datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, frozenset | set):
        return sorted(to_jsonable(v) for v in obj)
    return obj


class CalcResponse(BaseModel):
    calc_date: date
    result: dict[str, Any]
    rule_refs: list[dict[str, Any]]


# ---------------------------------------------------------------- 核价单


class SheetMeta(_In):
    product_name: str = Field(min_length=1, max_length=200)
    sku: str | None = Field(default=None, max_length=100)
    asin: str | None = Field(default=None, max_length=20)
    source_url: str | None = Field(default=None, max_length=2000)


class SheetCreate(SheetMeta):
    draft_input: dict[str, Any] | None = None
    """草稿允许不完整，不按 :class:`PricingInput` 严格校验；保存版本时才校验。"""


class SheetUpdate(_In):
    lock_version: int
    product_name: str | None = Field(default=None, min_length=1, max_length=200)
    sku: str | None = Field(default=None, max_length=100)
    asin: str | None = Field(default=None, max_length=20)
    source_url: str | None = Field(default=None, max_length=2000)
    draft_input: dict[str, Any] | None = None


class SaveVersionRequest(_In):
    lock_version: int
    client_token: str = Field(min_length=8, max_length=64)
    input: PricingInput


class SelectRequest(_In):
    version_id: int
    option_key: str = Field(min_length=1, max_length=80)
    note: str | None = Field(default=None, max_length=500)


class SheetListRequest(_In):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    q: str | None = Field(default=None, max_length=100)
    market: str | None = None
    status: Literal["draft", "saved"] | None = None


class VersionBrief(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    id: int
    version_no: int
    calc_date: date
    model_version: str
    created_at: datetime


class SelectionOut(BaseModel):
    market: str
    version_id: int
    option_key: str
    note: str | None
    selected_at: datetime


class SheetOut(BaseModel):
    id: int
    product_name: str
    sku: str | None
    asin: str | None
    source_url: str | None
    status: str
    draft_input: dict[str, Any] | None
    current_version_id: int | None
    lock_version: int
    created_at: datetime
    updated_at: datetime
    markets: list[str]
    rules_updated: bool
    """当前版本采用的规则里，有已被停用或被新版本取代的。"""
    selections: list[SelectionOut] = Field(default_factory=list)
    versions: list[VersionBrief] = Field(default_factory=list)


class SheetListResponse(BaseModel):
    items: list[SheetOut]
    total: int
    page: int
    page_size: int


class VersionOut(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    id: int
    sheet_id: int
    version_no: int
    calc_date: date
    model_version: str
    input: dict[str, Any]
    rule_refs: list[dict[str, Any]]
    result: dict[str, Any]
    created_at: datetime


# ---------------------------------------------------------------- 规则


class RuleCreate(_In):
    kind: Literal["fx_rate", "market_params", "referral_schedule", "fba_rate_card", "fbm_channel"]
    scope_key: str = Field(min_length=1, max_length=64)
    effective_from: date
    effective_to: date | None = None
    source: str = Field(min_length=1, max_length=2000)
    review_note: str | None = Field(default=None, max_length=4000)
    payload: dict[str, Any]


class RuleUpdate(_In):
    lock_version: int
    effective_from: date | None = None
    effective_to: date | None = None
    clear_effective_to: bool = False
    source: str | None = Field(default=None, min_length=1, max_length=2000)
    review_note: str | None = Field(default=None, max_length=4000)
    payload: dict[str, Any] | None = None


class RuleAction(_In):
    lock_version: int


class RuleRetire(_In):
    lock_version: int
    reason: str = Field(min_length=1, max_length=500)


class RuleListRequest(_In):
    kind: str | None = None
    scope_key: str | None = None
    status: Literal["draft", "active", "retired"] | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)


class RuleOut(BaseModel):
    id: int
    kind: str
    scope_key: str
    version: int
    status: str
    effective_from: date
    effective_to: date | None
    source: str
    review_note: str | None
    payload: dict[str, Any]
    payload_schema: int
    retired_reason: str | None
    lock_version: int
    created_at: datetime
    updated_at: datetime
    activated_at: datetime | None
    retired_at: datetime | None


class RuleListResponse(BaseModel):
    items: list[RuleOut]
    total: int
    page: int
    page_size: int
