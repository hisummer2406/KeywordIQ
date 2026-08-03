"""站点作用域查询的数据访问层（V2 星型模型）。

只读 fact_daily / fact_weekly（单站点单日期切片）+ dim_keyword / dim_product，
日期选择器与分页总数读 site_data_stats，类目下拉读 category_stats 物化视图。
无业务逻辑（作用域解析、默认站点等在 service 层）。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.table.analysis.dimension_model import DimKeyword, DimProduct
from app.table.analysis.fact_model import FactDaily, FactWeekly, SiteDataStats
from app.table.search.search_schemas import AnalysisSearchRequest
from config import settings

_SCHEMA = settings.DATABASE_SCHEMA

# 有筛选时的总数上限（沿用 V1 权衡：避免在整切片上精确计数）
FILTERED_COUNT_CAP = 10000

# 导出时服务端游标每次取回的行数。块内驻留内存，故不宜大；日视图还要按块回查走势，
# 太小则回查次数过多。
EXPORT_CHUNK_SIZE = 2000

# 排序白名单（两视图字段名相同）。日视图的 7 天走势/当周变化不在此列——它们是附加信息、
# 非结果集来源字段，收到就忽略、回落默认。
SORTABLE_FIELDS = frozenset(
    {
        "current_ranking",
        "ranking_change",
        "previous_ranking",
        "top1_click_share",
        "top1_conversion_share",
        "is_new",
    }
)
_DEFAULT_ORDER = "current_ranking"


def _fact_and_datecol(view: str):
    """按视图返回 (事实表模型, 日期列)。"""
    if view == "WEEK":
        return FactWeekly, FactWeekly.report_date_week
    return FactDaily, FactDaily.report_date


def request_has_filters(req: AnalysisSearchRequest) -> bool:
    """请求是否带任何筛选（决定总数走 site_data_stats 还是上限计数）。"""
    return any(
        v is not None
        for v in (
            req.keyword, req.brand, req.category, req.asin, req.product_title,
            req.category_exact, req.ranking_min, req.ranking_max, req.change_min,
            req.change_max, req.click_share_min, req.click_share_max,
            req.conversion_share_min, req.conversion_share_max, req.is_new,
        )
    )


def available_dates_stmt(site: str, view: str):
    """该 (站点, 视图) 有数据且在保留窗口内的日期，倒序。来源 site_data_stats。"""
    return (
        select(SiteDataStats.report_date)
        .where(SiteDataStats.site == site, SiteDataStats.period_type == view)
        .order_by(SiteDataStats.report_date.desc())
    )


def scope_row_count_stmt(site: str, view: str, on: date):
    """作用域内无筛选总数：直接读 site_data_stats.row_count（O(1)）。"""
    return select(SiteDataStats.row_count).where(
        SiteDataStats.site == site,
        SiteDataStats.period_type == view,
        SiteDataStats.report_date == on,
    )


def _base_query(site: str, view: str, on: date):
    """作用域恒定的 base query：单站点单日期切片 JOIN 维度。"""
    fact, date_col = _fact_and_datecol(view)
    stmt = (
        select(
            fact.keyword_id.label("keyword_id"),
            DimKeyword.keyword.label("keyword"),
            fact.current_ranking.label("current_ranking"),
            fact.previous_ranking.label("previous_ranking"),
            fact.ranking_change.label("ranking_change"),
            fact.is_new.label("is_new"),
            DimProduct.asin.label("top1_asin"),
            DimProduct.title.label("top1_title"),
            DimProduct.brand.label("top1_brand"),
            DimProduct.category.label("top1_category"),
            fact.top1_click_share.label("top1_click_share"),
            fact.top1_conversion_share.label("top1_conversion_share"),
        )
        .select_from(fact)
        .join(DimKeyword, DimKeyword.keyword_id == fact.keyword_id)
        .join(DimProduct, DimProduct.product_id == fact.top1_product_id, isouter=True)
        .where(fact.site == site, date_col == on)
    )
    return stmt, fact


def _apply_filters(stmt, fact, req: AnalysisSearchRequest):
    """在 base query 上 AND 叠加筛选。不触碰站点/日期边界。"""
    if req.keyword:
        stmt = stmt.where(DimKeyword.keyword.ilike(f"%{req.keyword}%"))
    if req.brand:
        stmt = stmt.where(DimProduct.brand.ilike(f"%{req.brand}%"))
    if req.category:
        stmt = stmt.where(DimProduct.category.ilike(f"%{req.category}%"))
    if req.category_exact:
        stmt = stmt.where(DimProduct.category == req.category_exact)
    if req.asin:
        stmt = stmt.where(DimProduct.asin.ilike(f"%{req.asin}%"))
    if req.product_title:
        stmt = stmt.where(DimProduct.title.ilike(f"%{req.product_title}%"))
    if req.ranking_min is not None:
        stmt = stmt.where(fact.current_ranking >= req.ranking_min)
    if req.ranking_max is not None:
        stmt = stmt.where(fact.current_ranking <= req.ranking_max)
    if req.change_min is not None:
        stmt = stmt.where(fact.ranking_change >= req.change_min)
    if req.change_max is not None:
        stmt = stmt.where(fact.ranking_change <= req.change_max)
    if req.click_share_min is not None:
        stmt = stmt.where(fact.top1_click_share >= req.click_share_min)
    if req.click_share_max is not None:
        stmt = stmt.where(fact.top1_click_share <= req.click_share_max)
    if req.conversion_share_min is not None:
        stmt = stmt.where(fact.top1_conversion_share >= req.conversion_share_min)
    if req.conversion_share_max is not None:
        stmt = stmt.where(fact.top1_conversion_share <= req.conversion_share_max)
    if req.is_new is not None:
        stmt = stmt.where(fact.is_new == req.is_new)
    return stmt


def _apply_order(stmt, fact, req: AnalysisSearchRequest):
    """排序白名单：非法字段回落默认（当期排名升序）。"""
    field = req.orderBy if req.orderBy in SORTABLE_FIELDS else _DEFAULT_ORDER
    col = getattr(fact, field)
    direction = req.orderDir or ("asc" if field == _DEFAULT_ORDER else "desc")
    col = col.asc() if direction == "asc" else col.desc()
    # 加 keyword_id 兜底，保证分页稳定
    return stmt.order_by(col, fact.keyword_id.asc())


def search_stmt(site: str, view: str, on: date, req: AnalysisSearchRequest):
    """主查询：作用域内一页数据（含筛选/排序/分页）。"""
    stmt, fact = _base_query(site, view, on)
    stmt = _apply_filters(stmt, fact, req)
    stmt = _apply_order(stmt, fact, req)
    return stmt.offset((req.page - 1) * req.perPage).limit(req.perPage)


def count_capped_stmt(site: str, view: str, on: date, req: AnalysisSearchRequest, cap: int):
    """作用域内筛选后的行数，最多数到 ``cap``（数满即返回 cap，不再往下扫）。

    列表分页与导出上限检查共用：两处都只需要"是否超过某个数"，不需要精确总数。
    """
    stmt, fact = _base_query(site, view, on)
    stmt = _apply_filters(stmt, fact, req)
    capped = stmt.with_only_columns(fact.keyword_id).limit(cap).subquery()
    return select(func.count()).select_from(capped)


def count_filtered_stmt(site: str, view: str, on: date, req: AnalysisSearchRequest):
    """有筛选时的总数：上限内精确计数（超过上限返回上限值）。"""
    return count_capped_stmt(site, view, on, req, FILTERED_COUNT_CAP)


def export_stmt(site: str, view: str, on: date, req: AnalysisSearchRequest, limit: int):
    """导出用语句：按作用域与筛选取行，带服务端游标的执行选项。

    ``yield_per`` 触发 ``stream_results``，内存与块大小同阶、不随结果集增长——GB 级
    切片下整表 fetch 会撑爆 8 GiB 的应用服务器。调用方按 partition 分块消费，
    好让上层能按块批量补齐日视图的走势/当周变化。
    """
    stmt, fact = _base_query(site, view, on)
    stmt = _apply_filters(stmt, fact, req)
    stmt = _apply_order(stmt, fact, req)
    return stmt.limit(limit).execution_options(yield_per=EXPORT_CHUNK_SIZE)


def trends_stmt(site: str, keyword_ids: list[int], on: date):
    """日视图近 7 天走势的语句：对本页 keyword_id 回溯查 fact_daily（回溯 6 天）。

    走 idx_fact_daily_keyword_history（site, keyword_id, report_date）；report_date 范围
    条件保证分区裁剪。历史不足 7 天就少几个点，不补齐。
    """
    start = on - timedelta(days=6)
    return (
        select(FactDaily.keyword_id, FactDaily.current_ranking)
        .where(
            FactDaily.site == site,
            FactDaily.keyword_id.in_(keyword_ids),
            FactDaily.report_date >= start,
            FactDaily.report_date <= on,
        )
        .order_by(FactDaily.keyword_id, FactDaily.report_date)
    )


def week_date_stmt(site: str, on: date):
    """把所选日期映射到其所属 ISO 周的周报期次。

    映射口径（查询侧决定，导入侧只存文件名日期、不定义周边界）：同 ISO 周
    （``date_trunc('week')`` 相等）。在 site_data_stats 小表上解析，避免碰事实表。
    """
    return text(
        f"SELECT report_date FROM {_SCHEMA}.site_data_stats "
        f"WHERE site = :site AND period_type = 'WEEK' "
        f"AND date_trunc('week', report_date) = date_trunc('week', :on) "
        f"ORDER BY report_date DESC LIMIT 1"
    ).bindparams(site=site, on=on)


def week_changes_stmt(site: str, week_date: date, keyword_ids: list[int]):
    """取某周报期次下各 keyword 的 ranking_change。等值条件利于分区裁剪。"""
    return select(FactWeekly.keyword_id, FactWeekly.ranking_change).where(
        FactWeekly.site == site,
        FactWeekly.report_date_week == week_date,
        FactWeekly.keyword_id.in_(keyword_ids),
    )


def category_options_stmt(site: str, view: str):
    """类目下拉：读 category_stats 物化视图，按 (站点, 周期) 返回选项与计数。"""
    return text(
        f"SELECT top_category, cnt FROM {_SCHEMA}.category_stats "
        f"WHERE site = :site AND period_type = :view ORDER BY cnt DESC"
    ).bindparams(site=site, view=view)


# --------------------------------------------------------------------------
# 整形：把结果行转成上层要的形状。纯函数，与执行模型无关。
# --------------------------------------------------------------------------


def shape_rows(rows) -> list[dict[str, Any]]:
    """结果行 → dict 列表。"""
    return [dict(row._mapping) for row in rows]


def shape_trends(rows) -> dict[int, list[int]]:
    """(keyword_id, ranking) 行 → {keyword_id: [排名…]}（按日期升序，依赖语句已排序）。"""
    out: dict[int, list[int]] = {}
    for kid, rank in rows:
        out.setdefault(kid, []).append(rank)
    return out


def shape_week_changes(rows) -> dict[int, int]:
    """(keyword_id, ranking_change) 行 → dict。"""
    return {kid: ch for kid, ch in rows}


def shape_category_options(rows) -> list[dict[str, Any]]:
    """(top_category, cnt) 行 → 下拉选项列表。"""
    return [{"category": r[0], "count": r[1]} for r in rows]


# --------------------------------------------------------------------------
# 同步执行器：导出路径与迁移/脚本用。
#
# 面向请求的查询路径走异步，直接用上面的构造函数 + ``await session.execute(...)``。
# 两条路径共用同一批构造函数——SQL 只有一处定义，作用域边界、筛选、排序白名单、
# 封顶计数不会漂移。差异被压缩到 ``await`` 与否这一行。
# --------------------------------------------------------------------------


def available_dates(db: Session, site: str, view: str) -> list[date]:
    return list(db.execute(available_dates_stmt(site, view)).scalars().all())


def scope_row_count(db: Session, site: str, view: str, on: date) -> int:
    return db.execute(scope_row_count_stmt(site, view, on)).scalar_one_or_none() or 0


def search_rows(
    db: Session, site: str, view: str, on: date, req: AnalysisSearchRequest
) -> list[dict[str, Any]]:
    return shape_rows(db.execute(search_stmt(site, view, on, req)).all())


def count_capped(
    db: Session, site: str, view: str, on: date, req: AnalysisSearchRequest, cap: int
) -> int:
    return db.execute(count_capped_stmt(site, view, on, req, cap)).scalar_one()


def count_filtered(db: Session, site: str, view: str, on: date, req: AnalysisSearchRequest) -> int:
    return count_capped(db, site, view, on, req, FILTERED_COUNT_CAP)


def iter_export_rows(
    db: Session, site: str, view: str, on: date, req: AnalysisSearchRequest, limit: int
):
    """导出：分块产出（每块一个 dict 列表）。保持同步——服务端游标 + 自建会话。"""
    result = db.execute(export_stmt(site, view, on, req, limit))
    for partition in result.partitions():
        yield shape_rows(partition)


def fetch_trends(db: Session, site: str, keyword_ids: list[int], on: date) -> dict[int, list[int]]:
    if not keyword_ids:
        return {}
    return shape_trends(db.execute(trends_stmt(site, keyword_ids, on)).all())


def fetch_week_changes(db: Session, site: str, keyword_ids: list[int], on: date) -> dict[int, int]:
    if not keyword_ids:
        return {}
    week_date = db.execute(week_date_stmt(site, on)).scalar()
    if week_date is None:
        return {}
    return shape_week_changes(db.execute(week_changes_stmt(site, week_date, keyword_ids)).all())


def category_options(db: Session, site: str, view: str) -> list[dict[str, Any]]:
    return shape_category_options(db.execute(category_options_stmt(site, view)).all())
