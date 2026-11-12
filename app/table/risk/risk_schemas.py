"""外观专利核查的请求模型。响应是 service 组装的 dict，形状见 ``check_service.serialize_*``。"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.table.risk.jurisdictions import MAX_DETAIL_BATCH, PAGE_SIZE_OPTIONS
from app.table.risk.risk_model import DECISIONS, DETAIL_KINDS, RISK_LEVELS

Token = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


def _strip(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    return v or None


class CheckCreate(BaseModel):
    """创建核查单的表单字段（图片另传）。"""

    model_config = ConfigDict(extra="forbid")

    product_name: str = Field(min_length=1, max_length=200)
    product_version: str = Field(min_length=1, max_length=100)
    source_ref: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=5000)
    markets: list[str] = Field(min_length=1, max_length=10)
    jurisdictions: list[str] = Field(min_length=1, max_length=20)
    page_size: int
    previous_check_id: int | None = None
    client_token: str = Token

    @field_validator("product_name", "product_version")
    @classmethod
    def _required_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("不能为空")
        return v

    @field_validator("source_ref", "description")
    @classmethod
    def _optional_text(cls, v: str | None) -> str | None:
        return _strip(v)

    @field_validator("markets", "jurisdictions")
    @classmethod
    def _codes(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for code in v:
            code = code.strip().upper()
            if code and code not in out:
                out.append(code)
        if not out:
            raise ValueError("至少选择一项")
        return out

    @field_validator("page_size")
    @classmethod
    def _page_size(cls, v: int) -> int:
        if v not in PAGE_SIZE_OPTIONS:
            raise ValueError(f"只能是 {PAGE_SIZE_OPTIONS}")
        return v


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_token: str = Token


class EvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_token: str = Token
    candidate_ids: list[int] = Field(min_length=1, max_length=MAX_DETAIL_BATCH)
    kinds: list[Literal["bibliography", "legal", "drawings", "pdf"]] = Field(
        default=["bibliography", "legal"], min_length=1,
    )

    @field_validator("candidate_ids")
    @classmethod
    def _dedupe(cls, v: list[int]) -> list[int]:
        return sorted(set(v))

    @field_validator("kinds")
    @classmethod
    def _dedupe_kinds(cls, v: list[str]) -> list[str]:
        return [k for k in DETAIL_KINDS if k in v]


class ReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_token: str = Token
    market: str = Field(min_length=2, max_length=8)
    risk_level: Literal[RISK_LEVELS]  # type: ignore[valid-type]
    decision: Literal[DECISIONS]  # type: ignore[valid-type]
    basis: str = Field(min_length=1, max_length=5000)
    uncovered_scope: str = Field(min_length=1, max_length=5000)
    conditions: str | None = Field(default=None, max_length=5000)
    evidence_candidate_ids: list[int] = Field(default_factory=list, max_length=200)
    review_date: date | None = None

    @field_validator("market")
    @classmethod
    def _market(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("basis", "uncovered_scope")
    @classmethod
    def _required_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("不能为空")
        return v

    @field_validator("conditions")
    @classmethod
    def _optional_text(cls, v: str | None) -> str | None:
        return _strip(v)

    @field_validator("evidence_candidate_ids")
    @classmethod
    def _dedupe(cls, v: list[int]) -> list[int]:
        return sorted(set(v))
