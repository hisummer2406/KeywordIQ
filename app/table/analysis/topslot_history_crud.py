"""ASIN/品牌悬浮历史的数据访问层（issue #96）。

与 `trend_crud.py` 同类——单 keyword_id 的历史回溯，走
`idx_fact_{daily,weekly}_keyword_history (site, keyword_id, 日期列)`，不扫全站榜单。
不同的是这里进一步在这个已收窄的行集合里，按「哪个槽位命中了给定身份」做残余过滤，
详见 design.md 决策 3、7。

**`keyword_site_stmt` 与 `trend_crud.keyword_site_stmt` 是同一条一行语句的两份拷贝。**
这是刻意的：它只是 `dim_keyword` 的主键查找，不是需要"只有一处定义"的业务口径（那条
纪律约束的是 `fact_metric_columns` 这类会漂移的投影/关联逻辑）。analysis 与 keyword
是两个独立分层的域，analysis 不反向依赖 keyword 模块。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import Select, case, func, or_, select

from app.table.analysis.dimension_model import DimBrand, DimKeyword, DimProduct
from app.table.analysis.topslot_history_schemas import TopslotKind

#: 按身份种类决定匹配哪一组三元槽位列名。两者结构相同，只有列名前缀不同。
_PRODUCT_ID_COLS = ("top1_product_id", "top2_product_id", "top3_product_id")
_BRAND_ID_COLS = ("top1_brand_id", "top2_brand_id", "top3_brand_id")


def keyword_site_stmt(keyword_id: int):
    """按 keyword_id 取其归属站点，供跨站点校验用。"""
    return select(DimKeyword.site).where(DimKeyword.keyword_id == keyword_id)


def resolve_asin_stmt(asin: str):
    """ASIN 原文 → product_id。`dim_product.asin` 有唯一约束，精确匹配 O(1)。"""
    return select(DimProduct.product_id).where(DimProduct.asin == asin)


def resolve_brand_stmt(name: str):
    """品牌原文 → brand_id。`dim_brand.name` 有唯一约束，精确匹配 O(1)。"""
    return select(DimBrand.brand_id).where(DimBrand.name == name)


def _slot_columns(fact, kind: TopslotKind):
    names = _PRODUCT_ID_COLS if kind == "ASIN" else _BRAND_ID_COLS
    return [getattr(fact, name) for name in names]


def _slot_match(fact, kind: TopslotKind, ident_id: int):
    """三个槽位列各自与身份 id 相等的条件，供 WHERE 与排名投影共用。"""
    return [col == ident_id for col in _slot_columns(fact, kind)]


def _best_rank_expr(fact, kind: TopslotKind, ident_id: int):
    """`LEAST(CASE WHEN col1=id THEN 1 END, ...)`——同一期多槽位命中时取最佳（最小）名次。

    `LEAST` 忽略 NULL、仅在全部为 NULL 时才返回 NULL；调用方的 WHERE 已保证至少一个
    槽位命中（非 NULL），故这里永远能算出确定的名次，不依赖数据库扫描顺序。见
    design.md 决策 3。
    """
    matches = _slot_match(fact, kind, ident_id)
    return func.least(*(case((m, i + 1)) for i, m in enumerate(matches)))


def _where(fact, date_col, kind: TopslotKind, ident_id: int, *, site: str, keyword_id: int,
           start: date, end: date):
    return [
        fact.site == site,
        fact.keyword_id == keyword_id,
        date_col >= start,
        date_col <= end,
        or_(*_slot_match(fact, kind, ident_id)),
    ]


def history_stmt(
    fact, date_col, kind: TopslotKind, ident_id: int, *,
    site: str, keyword_id: int, start: date, end: date, offset: int, limit: int,
) -> Select:
    """一页命中记录，按日期倒序。

    走 `idx_fact_{daily,weekly}_keyword_history (site, keyword_id, 日期列)`——
    `(site, keyword_id)` 等值前缀先把扫描范围收窄到这个关键词的全部历史行（保留窗口
    内至多约一两百行），日期范围与槽位匹配都是在这个小子集内的残余过滤，不触达事实表
    其余部分。
    """
    return (
        select(
            date_col.label("date"),
            _best_rank_expr(fact, kind, ident_id).label("rank"),
        )
        .where(*_where(fact, date_col, kind, ident_id, site=site, keyword_id=keyword_id,
                        start=start, end=end))
        .order_by(date_col.desc())
        .offset(offset)
        .limit(limit)
    )


def count_stmt(
    fact, date_col, kind: TopslotKind, ident_id: int, *,
    site: str, keyword_id: int, start: date, end: date,
) -> Select:
    """与 `history_stmt` 同条件的精确计数，不含 OFFSET/LIMIT。

    每次请求都重算而非只信首页：查询已被收窄到几十至上百行，重算代价可忽略
    （design.md 决策 6），换来的是不需要在前端假设"后续页 total 与首页一致"。
    """
    return select(func.count()).select_from(
        select(date_col)
        .where(*_where(fact, date_col, kind, ident_id, site=site, keyword_id=keyword_id,
                        start=start, end=end))
        .subquery()
    )


def shape_items(rows) -> list[dict]:
    return [{"date": r.date, "rank": r.rank} for r in rows]
