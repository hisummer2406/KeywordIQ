"""ASIN/品牌悬浮历史查询的语句结构测试（无需 PostgreSQL）。

覆盖 design.md 决策 3、7：查询先收窄到 (site, keyword_id, 日期范围)，槽位匹配用
`LEAST(CASE...)` 取最佳名次；决策 1：`kind` 决定匹配商品槽位还是品牌槽位；
决策 6：count 语句与 history 语句同条件但不含 OFFSET/LIMIT。
"""
from datetime import date

from app.table.analysis.fact_model import FactDaily, FactWeekly
from app.table.analysis.topslot_history_crud import (
    count_stmt,
    history_stmt,
    resolve_asin_stmt,
    resolve_brand_stmt,
)

_START, _END = date(2026, 1, 1), date(2026, 7, 1)


def _sql(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


def test_asin_history_matches_all_three_product_slots():
    sql = _sql(
        history_stmt(
            FactDaily, FactDaily.report_date, "ASIN", 42,
            site="US", keyword_id=7, start=_START, end=_END, offset=0, limit=20,
        )
    )
    assert "top1_product_id = 42" in sql
    assert "top2_product_id = 42" in sql
    assert "top3_product_id = 42" in sql
    assert "brand_id" not in sql


def test_brand_history_matches_all_three_brand_slots_not_product():
    sql = _sql(
        history_stmt(
            FactDaily, FactDaily.report_date, "BRAND", 5,
            site="US", keyword_id=7, start=_START, end=_END, offset=0, limit=20,
        )
    )
    assert "top1_brand_id = 5" in sql
    assert "top2_brand_id = 5" in sql
    assert "top3_brand_id = 5" in sql
    assert "product_id" not in sql


def test_history_scopes_to_site_keyword_and_date_range_before_slot_match():
    """(site, keyword_id) 前缀条件必须存在，且日期范围用 BETWEEN 语义的 >=/<=——
    这是命中 idx_fact_daily_keyword_history 前缀索引的前提（design.md 决策 7）。
    """
    sql = _sql(
        history_stmt(
            FactDaily, FactDaily.report_date, "ASIN", 42,
            site="US", keyword_id=7, start=_START, end=_END, offset=0, limit=20,
        )
    )
    assert "fact_daily.site = 'US'" in sql
    assert "fact_daily.keyword_id = 7" in sql
    assert "report_date >= '2026-01-01'" in sql
    assert "report_date <= '2026-07-01'" in sql


def test_history_orders_by_date_descending():
    sql = _sql(
        history_stmt(
            FactDaily, FactDaily.report_date, "ASIN", 42,
            site="US", keyword_id=7, start=_START, end=_END, offset=0, limit=20,
        )
    )
    assert "ORDER BY analysis.fact_daily.report_date DESC" in sql


def test_history_uses_offset_and_limit_for_pagination():
    sql = _sql(
        history_stmt(
            FactDaily, FactDaily.report_date, "ASIN", 42,
            site="US", keyword_id=7, start=_START, end=_END, offset=40, limit=20,
        )
    )
    assert "LIMIT 20" in sql
    assert "OFFSET 40" in sql


def test_best_rank_uses_least_of_case_expressions():
    """同一期多槽位命中时取最佳（数值最小）名次，见 design.md 决策 3。"""
    sql = _sql(
        history_stmt(
            FactDaily, FactDaily.report_date, "ASIN", 42,
            site="US", keyword_id=7, start=_START, end=_END, offset=0, limit=20,
        )
    )
    assert "least(CASE WHEN" in sql
    assert "THEN 1 END" in sql
    assert "THEN 2 END" in sql
    assert "THEN 3 END" in sql


def test_count_stmt_shares_where_but_has_no_offset_or_limit():
    sql = _sql(
        count_stmt(
            FactDaily, FactDaily.report_date, "ASIN", 42,
            site="US", keyword_id=7, start=_START, end=_END,
        )
    )
    assert "count(*)" in sql.lower()
    assert "top1_product_id = 42" in sql
    assert "LIMIT" not in sql
    assert "OFFSET" not in sql


def test_weekly_view_targets_fact_weekly_and_its_week_date_column():
    """周报分区列是 report_date_week，不是 report_date——照抄日报列名会
    UndefinedColumn（CLAUDE.md「分区列名两表不同」）。
    """
    sql = _sql(
        history_stmt(
            FactWeekly, FactWeekly.report_date_week, "ASIN", 42,
            site="US", keyword_id=7, start=_START, end=_END, offset=0, limit=20,
        )
    )
    assert "analysis.fact_weekly" in sql
    assert "report_date_week" in sql


def test_resolve_asin_stmt_matches_exact_asin_column():
    sql = _sql(resolve_asin_stmt("B0EXAMPLE1"))
    assert "dim_product.asin = 'B0EXAMPLE1'" in sql


def test_resolve_brand_stmt_matches_exact_brand_name_column():
    sql = _sql(resolve_brand_stmt("Acme Corp"))
    assert "dim_brand.name = 'Acme Corp'" in sql
