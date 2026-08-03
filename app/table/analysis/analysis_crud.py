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


def available_dates(db: Session, site: str, view: str) -> list[date]:
    """该 (站点, 视图) 有数据且在保留窗口内的日期，倒序。来源 site_data_stats。"""
    stmt = (
        select(SiteDataStats.report_date)
        .where(SiteDataStats.site == site, SiteDataStats.period_type == view)
        .order_by(SiteDataStats.report_date.desc())
    )
    return list(db.execute(stmt).scalars().all())


def scope_row_count(db: Session, site: str, view: str, on: date) -> int:
    """作用域内无筛选总数：直接读 site_data_stats.row_count（O(1)）。"""
    stmt = select(SiteDataStats.row_count).where(
        SiteDataStats.site == site,
        SiteDataStats.period_type == view,
        SiteDataStats.report_date == on,
    )
    return db.execute(stmt).scalar_one_or_none() or 0


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


def search_rows(db: Session, site: str, view: str, on: date, req: AnalysisSearchRequest) -> list[dict[str, Any]]:
    """主查询：作用域内一页数据（含筛选/排序/分页）。"""
    stmt, fact = _base_query(site, view, on)
    stmt = _apply_filters(stmt, fact, req)
    stmt = _apply_order(stmt, fact, req)
    stmt = stmt.offset((req.page - 1) * req.perPage).limit(req.perPage)
    return [dict(row._mapping) for row in db.execute(stmt).all()]


def count_capped(
    db: Session, site: str, view: str, on: date, req: AnalysisSearchRequest, cap: int
) -> int:
    """作用域内筛选后的行数，最多数到 ``cap``（数满即返回 cap，不再往下扫）。

    列表分页与导出上限检查共用：两处都只需要"是否超过某个数"，不需要精确总数。
    """
    stmt, fact = _base_query(site, view, on)
    stmt = _apply_filters(stmt, fact, req)
    capped = stmt.with_only_columns(fact.keyword_id).limit(cap).subquery()
    return db.execute(select(func.count()).select_from(capped)).scalar_one()


def count_filtered(db: Session, site: str, view: str, on: date, req: AnalysisSearchRequest) -> int:
    """有筛选时的总数：上限内精确计数（超过上限返回上限值）。"""
    return count_capped(db, site, view, on, req, FILTERED_COUNT_CAP)


def iter_export_rows(
    db: Session, site: str, view: str, on: date, req: AnalysisSearchRequest, limit: int
):
    """导出用：按作用域与筛选流式取行，分块产出（每块一个 dict 列表）。

    用服务端游标（``yield_per`` 触发 ``stream_results``）逐块拉取，内存与块大小同阶，
    不随结果集增长——GB 级切片下整表 fetch 会撑爆 8 GiB 的应用服务器。
    分块而非逐行产出，是为了让上层能按块批量补齐日视图的走势/当周变化。
    """
    stmt, fact = _base_query(site, view, on)
    stmt = _apply_filters(stmt, fact, req)
    stmt = _apply_order(stmt, fact, req)
    stmt = stmt.limit(limit)
    result = db.execute(stmt.execution_options(yield_per=EXPORT_CHUNK_SIZE))
    for partition in result.partitions():
        yield [dict(row._mapping) for row in partition]


def fetch_trends(db: Session, site: str, keyword_ids: list[int], on: date) -> dict[int, list[int]]:
    """日视图近 7 天走势：对本页 keyword_id 回溯查 fact_daily（截至所选日期回溯 6 天）。

    走 idx_fact_daily_keyword_history（site, keyword_id, report_date）；report_date 范围
    条件保证分区裁剪。历史不足 7 天就少几个点，不补齐。返回 {keyword_id: [排名…]}（按日期升序）。
    """
    if not keyword_ids:
        return {}
    start = on - timedelta(days=6)
    rows = db.execute(
        select(FactDaily.keyword_id, FactDaily.current_ranking)
        .where(
            FactDaily.site == site,
            FactDaily.keyword_id.in_(keyword_ids),
            FactDaily.report_date >= start,
            FactDaily.report_date <= on,
        )
        .order_by(FactDaily.keyword_id, FactDaily.report_date)
    ).all()
    out: dict[int, list[int]] = {}
    for kid, rank in rows:
        out.setdefault(kid, []).append(rank)
    return out


def fetch_week_changes(db: Session, site: str, keyword_ids: list[int], on: date) -> dict[int, int]:
    """当周排名变化：把所选日期映射到其所属 ISO 周的周报期次，取该期各 keyword 的 ranking_change。

    映射口径（查询侧决定，导入侧只存文件名日期不定义周边界）：同 ISO 周
    （date_trunc('week') 相等）。先在 site_data_stats 小表解析出周报期次 W，再等值查
    fact_weekly（等值条件利于分区裁剪）。当周周报缺失 → 返回空 dict（该列全为空）。
    """
    if not keyword_ids:
        return {}
    week_date = db.execute(
        text(
            f"SELECT report_date FROM {_SCHEMA}.site_data_stats "
            f"WHERE site = :site AND period_type = 'WEEK' "
            f"AND date_trunc('week', report_date) = date_trunc('week', :on) "
            f"ORDER BY report_date DESC LIMIT 1"
        ),
        {"site": site, "on": on},
    ).scalar()
    if week_date is None:
        return {}
    rows = db.execute(
        select(FactWeekly.keyword_id, FactWeekly.ranking_change).where(
            FactWeekly.site == site,
            FactWeekly.report_date_week == week_date,
            FactWeekly.keyword_id.in_(keyword_ids),
        )
    ).all()
    return {kid: ch for kid, ch in rows}


def category_options(db: Session, site: str, view: str) -> list[dict[str, Any]]:
    """类目下拉：读 category_stats 物化视图，按 (站点, 周期) 返回选项与计数。"""
    rows = db.execute(
        text(
            f"SELECT top_category, cnt FROM {_SCHEMA}.category_stats "
            f"WHERE site = :site AND period_type = :view ORDER BY cnt DESC"
        ),
        {"site": site, "view": view},
    ).all()
    return [{"category": r[0], "count": r[1]} for r in rows]
