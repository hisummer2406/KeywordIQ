"""关键词排名趋势详情的请求/响应 DTO（市场分析与我的词库共用）。

详情固定单个关键词 + 单个站点，按视图（DAY/WEEK）只读对应事实表；不混用、不拼接、
不聚合两个视图。见 openspec 变更 `add-keyword-trend-detail`。
"""
from __future__ import annotations

from datetime import date as _Date
from typing import Literal

from pydantic import BaseModel, Field

TrendRange = Literal["RECENT_30D", "ALL"]

#: 五态：见 design.md 决策 2。RANKED/DROPPED 携带排名，其余三态排名恒为空。
TrendPointStatus = Literal["RANKED", "DROPPED", "NO_DATA", "NOT_IMPORTED", "OUT_OF_RETENTION"]


class KeywordTrendPoint(BaseModel):
    """时间轴上的一个期次。真实日期，不做等距排布——那是展示层的事。"""

    date: _Date
    status: TrendPointStatus
    # 只有 RANKED 才有值；DROPPED 的"最后在榜排名"不进这个字段，那是另一段历史的
    # 数据，混进来会让"这一格是哪天的排名"变得含糊（与事实表掉榜行的既有口径一致）。
    ranking: int | None = None


class KeywordTrendStat(BaseModel):
    """区间统计点：某个排名 + 它对应的日期。"""

    date: _Date
    ranking: int


class KeywordTrendCoverage(BaseModel):
    """实际数据覆盖范围（RANKED/DROPPED/NO_DATA 期次的最早与最晚日期）。

    与"所选时间范围"是两回事：所选范围可能比实际覆盖更宽（比如新站点选了「全部可用
    历史」但只有两个月数据）。前端必须分别展示，否则用户会以为系统弄丢了数据。
    """

    start: _Date
    end: _Date


class FirstEvidence(BaseModel):
    """一项首次证据。``basis``：IMPORTED 确认首次 / EARLIEST_OBSERVED 最早可观测（此前可能已出现）
    / UNKNOWN 未知 / ABSENT 未上过该榜（仅日榜、周榜两项）。"""

    date: _Date | None = None
    basis: str
    period_type: Literal["DAY", "WEEK"] | None = Field(
        None, description="仅共享监测首次：日期来自日报还是周报"
    )


class KeywordFirstEvidence(BaseModel):
    """日榜首次、周榜首次与首次进入监测（共享记录）三项，并列解释「为什么（不）是新词」。"""

    day: FirstEvidence
    week: FirstEvidence
    monitor: FirstEvidence


class KeywordTrendResponse(BaseModel):
    """趋势详情响应。"""

    success: bool = True
    keyword_id: int
    keyword: str
    site: str
    view: Literal["DAY", "WEEK"]
    range: TrendRange
    # 实际使用的锚定期。``None`` 仅当该 (site, view) 完全没有任何数据。
    on: _Date | None = None
    coverage: KeywordTrendCoverage | None = None
    points: list[KeywordTrendPoint] = Field(default_factory=list)
    # 只统计 RANKED 点；选定范围内没有任何 RANKED 点时两者均为 None——0 不能拿来
    # 编码"无数据"，它在排名附近是一个合法的普通数字。
    best_ranking: KeywordTrendStat | None = None
    latest_available: KeywordTrendStat | None = None
    first_evidence: KeywordFirstEvidence | None = None
