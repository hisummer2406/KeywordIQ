"""关键词排名趋势详情的数据访问层。

与 ``analysis_crud.trends_stmt`` 刻意分开：那条语句是为"一页几十个 keyword_id、固定
小窗口"的批量回溯设计的；这里是"一个 keyword_id、窗口可到全部保留历史"，形状不同，
硬塞进同一个函数会把两种假设混在一起。两者共用 ``analysis_crud.fact_and_datecol``
拿事实表与日期列，SQL 的"分区列名两表不同"这条纪律只有那一处定义，不在这里重写。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.table.analysis.analysis_crud import fact_and_datecol
from app.table.analysis.dimension_model import DimKeyword
from app.table.analysis.fact_model import FactDropped


def keyword_site_stmt(keyword_id: int):
    """按 keyword_id 取其归属站点与关键词原文，供跨站点校验与响应回显用。"""
    return select(DimKeyword.site, DimKeyword.keyword).where(DimKeyword.keyword_id == keyword_id)


def keyword_history_stmt(site: str, view: str, keyword_id: int, start: date, end: date):
    """单关键词在 [start, end] 内的当期排名序列。

    走 ``idx_fact_{daily,weekly}_keyword_history (site, keyword_id, 日期列)``——为批量
    趋势建的索引，前缀 (site, keyword_id) 先把范围收窄到几十/几百行，日期范围再进一步收窄，
    单关键词查询同样命中，不需要另建索引。
    """
    fact, date_col = fact_and_datecol(view)
    return (
        select(date_col, fact.current_ranking)
        .where(
            fact.site == site,
            fact.keyword_id == keyword_id,
            date_col >= start,
            date_col <= end,
        )
        .order_by(date_col)
    )


def keyword_dropped_stmt(site: str, view: str, keyword_id: int, start: date, end: date):
    """单关键词在 [start, end] 内被判定为掉榜的期次（anchor_date 落在区间内）。

    走 ``fact_dropped`` 主键 ``(site, period_type, anchor_date, keyword_id)``：
    (site, period_type) 等值 + anchor_date 范围做索引扫描，keyword_id 等值在扫描内
    做 filter——`fact_dropped` 比事实表小一到两个数量级，这个 filter 代价可忽略。
    """
    return (
        select(FactDropped.anchor_date, FactDropped.last_ranking)
        .where(
            FactDropped.site == site,
            FactDropped.period_type == view,
            FactDropped.keyword_id == keyword_id,
            FactDropped.anchor_date >= start,
            FactDropped.anchor_date <= end,
        )
        .order_by(FactDropped.anchor_date)
    )
