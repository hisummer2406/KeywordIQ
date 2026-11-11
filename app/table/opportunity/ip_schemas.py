"""IP 专家 Agent 的请求模型。响应是 service 组装的 dict。

人工意见与单令牌请求直接复用 #24 的 ``ReviewCreate``、``TokenRequest``（决策 8）。
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.table.opportunity.agent_prompts import MAX_QUERIES, valid_query_text
from app.table.opportunity.ip_model import DETAIL_TOOLS
from app.table.risk.risk_schemas import ReviewCreate, Token, TokenRequest

__all__ = [
    "DetailRequest", "MessageCreate", "MoveRequest", "QueryItem", "ResearchCreate", "ReviewCreate",
    "ScheduleUpsert", "SearchRequest", "TokenRequest", "WorkspaceCreate", "WorkspaceRename",
]

QUERY_LIMITS = (10, 20, 50)
#: 一次取证最多选几件（每件按所选工具各调用一次 MCP）。
MAX_DETAIL_BATCH = 20
_LOC = re.compile(r"^\d{2}-\d{2}$")


def _codes(v: list[str]) -> list[str]:
    out: list[str] = []
    for code in v:
        code = code.strip().upper()
        if code and code not in out:
            out.append(code)
    if not out:
        raise ValueError("至少选择一项")
    return out


class ResearchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_token: str = Token
    product_name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    loc_classes: list[str] = Field(default_factory=list, max_length=10)
    jurisdictions: list[str] = Field(min_length=1, max_length=20)
    markets: list[str] = Field(min_length=1, max_length=10)
    #: 组织方式，不属于调研输入（不进输入指纹）
    workspace_id: int | None = None

    @field_validator("product_name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("不能为空")
        return v

    @field_validator("description")
    @classmethod
    def _desc(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        return v or None

    @field_validator("keywords")
    @classmethod
    def _keywords(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for k in v:
            k = " ".join(k.split())
            if len(k) > 100:
                raise ValueError("单个关键词不超过 100 字符")
            if k and k not in out:
                out.append(k)
        return out

    @field_validator("loc_classes")
    @classmethod
    def _loc(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for c in v:
            c = c.strip()
            if not _LOC.match(c):
                raise ValueError("洛迦诺分类格式为 07-01")
            if c not in out:
                out.append(c)
        return out

    @field_validator("jurisdictions", "markets")
    @classmethod
    def _code_list(cls, v: list[str]) -> list[str]:
        return _codes(v)


class QueryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_text: str
    limit: Literal[QUERY_LIMITS] = 20  # type: ignore[valid-type]

    @field_validator("query_text")
    @classmethod
    def _q(cls, v: str) -> str:
        q = valid_query_text(v)
        if q is None:
            raise ValueError("检索式须为 1–500 个字符，且不含控制字符")
        return q


class SearchRequest(BaseModel):
    """人工确认后执行的检索式（决策 1：Agent 方案只作建议，不会自动执行）。"""

    model_config = ConfigDict(extra="forbid")

    client_token: str = Token
    queries: list[QueryItem] = Field(min_length=1, max_length=MAX_QUERIES)

    @field_validator("queries")
    @classmethod
    def _dedupe(cls, v: list[QueryItem]) -> list[QueryItem]:
        seen: set[str] = set()
        out = []
        for q in v:
            if q.query_text not in seen:
                seen.add(q.query_text)
                out.append(q)
        return out


class DetailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_token: str = Token
    candidate_ids: list[int] = Field(min_length=1, max_length=MAX_DETAIL_BATCH)
    tools: list[Literal[DETAIL_TOOLS]] = Field(default=list(DETAIL_TOOLS), min_length=1)  # type: ignore[valid-type]

    @field_validator("candidate_ids")
    @classmethod
    def _ids(cls, v: list[int]) -> list[int]:
        return sorted(set(v))

    @field_validator("tools")
    @classmethod
    def _tools(cls, v: list[str]) -> list[str]:
        return [t for t in DETAIL_TOOLS if t in v]


MAX_MESSAGE_LEN = 2000


def _name(v: str) -> str:
    v = " ".join(v.split())
    if not v:
        raise ValueError("不能为空")
    return v


class WorkspaceCreate(BaseModel):
    """同名由唯一约束挡住，重复点击得到 409，不需要客户端令牌。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=50)

    @field_validator("name")
    @classmethod
    def _n(cls, v: str) -> str:
        return _name(v)


class WorkspaceRename(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=50)

    @field_validator("name")
    @classmethod
    def _n(cls, v: str) -> str:
        return _name(v)


class MoveRequest(BaseModel):
    """把任务移入某个工作空间；``workspace_id=None`` 即移出为未归类。"""

    model_config = ConfigDict(extra="forbid")

    workspace_id: int | None


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_token: str = Token
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_LEN)

    @field_validator("content")
    @classmethod
    def _c(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("不能为空")
        return v


class ScheduleUpsert(BaseModel):
    """定时复检（决策 13）。检索式必须是本调研已成功执行过的，由 service 校验。"""

    model_config = ConfigDict(extra="forbid")

    frequency: Literal["weekly", "monthly"]
    weekday: int | None = Field(default=None, ge=0, le=6)
    day_of_month: int | None = Field(default=None, ge=1, le=28)
    hour: int = Field(ge=0, le=23)
    queries: list[QueryItem] = Field(min_length=1, max_length=MAX_QUERIES)
    enabled: bool = True

    @field_validator("queries")
    @classmethod
    def _dedupe(cls, v: list[QueryItem]) -> list[QueryItem]:
        return SearchRequest._dedupe(v)

    @model_validator(mode="after")
    def _when(self) -> ScheduleUpsert:
        if self.frequency == "weekly":
            if self.weekday is None:
                raise ValueError("每周复检须指定周几")
            self.day_of_month = None
        else:
            if self.day_of_month is None:
                raise ValueError("每月复检须指定 1–28 日")
            self.weekday = None
        return self
