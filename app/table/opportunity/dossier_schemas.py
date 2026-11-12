"""机会档案的请求模型（Pydantic v2）。响应由 service 直接组装成字典。"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.table.opportunity.dossier_model import MANUAL_CATEGORIES, MAX_KEYWORDS, VERDICTS

_WS = re.compile(r"\s+")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TOKEN = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")

Verdict = Literal[VERDICTS]  # type: ignore[valid-type]
ManualCategory = Literal[MANUAL_CATEGORIES]  # type: ignore[valid-type]


def normalize_keyword(raw: str) -> str:
    """去首尾空白、压缩连续空白、转小写——档案内判重与解析 ``dim_keyword`` 都用它。"""
    return _WS.sub(" ", raw.strip()).lower()


def _clean_text(v: str | None) -> str | None:
    if v is None:
        return None
    v = _CTRL.sub("", v).strip()
    return v or None


def _keywords(values: list[str]) -> list[str]:
    out: list[str] = []
    for raw in values:
        kw = normalize_keyword(_CTRL.sub("", raw))
        if not kw:
            continue
        if len(kw) > 500:
            raise ValueError("单个关键词不能超过 500 个字符")
        if kw not in out:
            out.append(kw)
    return out


class DossierCreate(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    idea: str | None = Field(default=None, max_length=4000)
    site: str | None = Field(default=None, max_length=8, description="从词库分组建档时可省略，取分组的站点")
    keywords: list[str] = Field(default_factory=list, max_length=100)
    keyword_group_id: int | None = Field(default=None, ge=1, description="从「我的词库」的一个分组带入关键词")
    client_token: str = _TOKEN

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        v = _clean_text(v) or ""
        if not v:
            raise ValueError("标题不能为空")
        return v

    @field_validator("idea")
    @classmethod
    def _idea(cls, v: str | None) -> str | None:
        return _clean_text(v)

    @field_validator("keywords")
    @classmethod
    def _kw(cls, v: list[str]) -> list[str]:
        return _keywords(v)

    @model_validator(mode="after")
    def _source(self) -> DossierCreate:
        if self.keyword_group_id is None:
            if not self.site:
                raise ValueError("请选择站点")
            if not self.keywords:
                raise ValueError("至少填写一个关键词")
        if len(self.keywords) > MAX_KEYWORDS:
            raise ValueError(f"一个档案最多 {MAX_KEYWORDS} 个关键词")
        return self


class DossierUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    idea: str | None = Field(default=None, max_length=4000)
    #: True = 归档，False = 取消归档
    archived: bool | None = None
    #: True = 把负责人改成自己
    take_over: bool | None = None

    @field_validator("title")
    @classmethod
    def _title(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = _clean_text(v)
        if not v:
            raise ValueError("标题不能为空")
        return v

    @field_validator("idea")
    @classmethod
    def _idea(cls, v: str | None) -> str | None:
        # 空串表示清空说明
        return None if v is None else (_clean_text(v) or "")


class KeywordsAdd(BaseModel):
    keywords: list[str] = Field(min_length=1, max_length=MAX_KEYWORDS)

    @field_validator("keywords")
    @classmethod
    def _kw(cls, v: list[str]) -> list[str]:
        out = _keywords(v)
        if not out:
            raise ValueError("至少填写一个关键词")
        return out


class ManualEvidenceCreate(BaseModel):
    category: ManualCategory
    content: str = Field(min_length=1, max_length=4000)
    source_name: str = Field(min_length=1, max_length=100)
    source_url: str | None = Field(default=None, max_length=500)
    observed_date: date

    @field_validator("content", "source_name")
    @classmethod
    def _required(cls, v: str) -> str:
        v = _clean_text(v) or ""
        if not v:
            raise ValueError("不能为空")
        return v

    @field_validator("source_url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        v = _clean_text(v)
        if v is None:
            return None
        if not re.match(r"^https?://[^\s]+$", v, re.IGNORECASE):
            raise ValueError("来源链接只接受 http 或 https 地址")
        return v


class DecisionCreate(BaseModel):
    verdict: Verdict
    reason: str = Field(min_length=1, max_length=4000)
    next_step: str | None = Field(default=None, max_length=2000)
    recheck_date: date | None = None
    client_token: str = _TOKEN

    @field_validator("reason")
    @classmethod
    def _reason(cls, v: str) -> str:
        v = _clean_text(v) or ""
        if not v:
            raise ValueError("理由不能为空")
        return v

    @field_validator("next_step")
    @classmethod
    def _next(cls, v: str | None) -> str | None:
        return _clean_text(v)


class AskCreate(BaseModel):
    question: str = Field(min_length=1, max_length=1000)

    @field_validator("question")
    @classmethod
    def _q(cls, v: str) -> str:
        v = _clean_text(v) or ""
        if not v:
            raise ValueError("问题不能为空")
        return v
