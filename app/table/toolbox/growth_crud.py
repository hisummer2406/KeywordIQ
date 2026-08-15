"""持续增长榜的数据访问层：纯语句组装，不含业务判断、不提交事务。

榜单本身是导入时算定的（``importer.load_growth``），这里**只读**——不做窗口聚合、不做
反连接。这是这条路径成立的全部前提：实时算是一次 210 万行上的窗口聚合，且每翻一页重跑
一次。
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select

from app.table.analysis.dimension_model import DimKeyword
from app.table.analysis.fact_model import FactDaily, FactGrowth, SiteDataStats

# 排序字段 → 列。白名单在 schema 的 Literal 上已挡过一道，这里是第二道：
# 直接用请求里的字符串拼 SQL 是注入面，映射表让「可排序的列」只有这一处答案。
SORTABLE_COLUMNS = {
    "rank_gain": FactGrowth.rank_gain,
    "min_growth_rate": FactGrowth.min_growth_rate,
    "end_ranking": FactGrowth.end_ranking,
}

_PERIOD_DAY = "DAY"


def window_start(anchor: date, window_days: int) -> date:
    """窗口左端。窗口含锚定日本身，故回溯 ``window_days - 1`` 天。"""
    return anchor - timedelta(days=window_days - 1)


def available_dates_stmt(site: str):
    """该站点有日报数据、且在保留窗口内的日期，倒序。

    「哪些日期有榜单」直接等于「哪些日期有日报」，故复用 ``site_data_stats``，不为榜单
    单独建一份日期来源——两份日期清单迟早对不上，而对不上的表现是日期选择器里选得到、
    点下去却空白。
    """
    return (
        select(SiteDataStats.report_date)
        .where(SiteDataStats.site == site, SiteDataStats.period_type == _PERIOD_DAY)
        .order_by(SiteDataStats.report_date.desc())
    )


def window_present_dates_stmt(site: str, anchor: date, window_days: int):
    """窗口内**实际有数据**的期次，升序。缺期由服务层与完整窗口做差得出。"""
    return (
        select(SiteDataStats.report_date)
        .where(
            SiteDataStats.site == site,
            SiteDataStats.period_type == _PERIOD_DAY,
            SiteDataStats.report_date >= window_start(anchor, window_days),
            SiteDataStats.report_date <= anchor,
        )
        .order_by(SiteDataStats.report_date)
    )


def count_stmt(site: str, on: date):
    """榜单行数。

    直接 ``count(*)`` 而不像事实表那样预存：榜单每期只有数千行，走
    ``idx_fact_growth_rank (site, anchor_date, rank_gain)`` 的前缀是毫秒级。
    ``site_data_stats`` 存在的理由是躲开 8 亿行上的实时聚合，这里不是同一个问题，
    为它加一列预存计数只会多出一处必须与写入保持一致的状态。
    """
    return select(func.count()).select_from(FactGrowth).where(
        FactGrowth.site == site, FactGrowth.anchor_date == on
    )


def list_stmt(site: str, on: date, *, order_column, descending: bool, offset: int, limit: int):
    """一页榜单。关键词从维度表取，其余全部来自算定结果。

    次级排序键固定为 ``keyword_id``：主排序键（如净增名次）会有大量并列值，没有稳定的
    次级键时同一页在两次请求间可能返回不同的行——翻页翻出重复或漏行，且完全看不出原因。
    """
    order = order_column.desc() if descending else order_column.asc()
    return (
        select(
            FactGrowth.keyword_id,
            DimKeyword.keyword,
            FactGrowth.start_ranking,
            FactGrowth.end_ranking,
            FactGrowth.rank_gain,
            FactGrowth.min_growth_rate,
        )
        .join(DimKeyword, DimKeyword.keyword_id == FactGrowth.keyword_id)
        .where(FactGrowth.site == site, FactGrowth.anchor_date == on)
        .order_by(order, FactGrowth.keyword_id)
        .offset(offset)
        .limit(limit)
    )


def trends_stmt(site: str, keyword_ids: list[int], on: date, window_days: int):
    """当页各行的窗口内排名走势。走 ``idx_fact_daily_keyword_history``。

    **回溯范围与窗口完全相同**，因此取回的点原样就是要画的点，不需要裁剪。分析屏那边
    回溯范围宽于趋势窗口（为了顺带喂历史参照点），漏掉裁剪会画出多余的点而无人报错；
    这里没有那个失败模式，也就不要为了「和那边一致」而引入一层裁剪。
    """
    return (
        select(FactDaily.keyword_id, FactDaily.report_date, FactDaily.current_ranking)
        .where(
            FactDaily.site == site,
            FactDaily.keyword_id.in_(keyword_ids),
            FactDaily.report_date >= window_start(on, window_days),
            FactDaily.report_date <= on,
        )
        .order_by(FactDaily.keyword_id, FactDaily.report_date)
    )


def shape_trends(rows) -> tuple[dict[int, list[int]], dict[int, list[date]]]:
    """把回溯行按关键词聚成 (排名序列, 期次序列)。语句已按 (keyword_id, 日期) 排好序。"""
    ranks: dict[int, list[int]] = {}
    dates: dict[int, list[date]] = {}
    for keyword_id, on, ranking in rows:
        ranks.setdefault(keyword_id, []).append(ranking)
        dates.setdefault(keyword_id, []).append(on)
    return ranks, dates
