"""新词需求（issue #119）：品牌词排除、类目下拉隐藏的单元测试（无需 PostgreSQL）。

品牌名进正则的转义与整词边界由 test_new_word_rules_integration.py 在真库验证。
"""
from app.table.analysis.analysis_crud import (
    HIDDEN_CATEGORY_NAMES,
    request_has_filters,
    shape_category_options,
)
from app.table.search.search_schemas import AnalysisSearchRequest


def _req(**kw) -> AnalysisSearchRequest:
    return AnalysisSearchRequest(site="US", view="DAY", **kw)


def test_exclude_brand_counts_as_a_filter():
    """它确实过滤行：漏计会让总数仍报告站点当期总词数、页码全部对不上。"""
    assert request_has_filters(_req(exclude_brand_in_keyword=True)) is True


def test_exclude_brand_false_or_absent_is_not_a_filter():
    assert request_has_filters(_req(exclude_brand_in_keyword=False)) is False
    assert request_has_filters(_req()) is False


def test_digital_ebook_category_is_hidden_from_options_only():
    rows = [("Digital_Ebook_Purchase", 900), ("Electronics", 10), ("digital_ebook_purchase", 5)]
    options = shape_category_options(rows)
    assert [o["category"] for o in options] == ["Electronics"]
    assert "digital_ebook_purchase" in HIDDEN_CATEGORY_NAMES


def test_other_categories_are_kept_in_order_with_counts():
    rows = [("Home", 3), ("Pet Supplies", 2)]
    assert shape_category_options(rows) == [
        {"category": "Home", "count": 3},
        {"category": "Pet Supplies", "count": 2},
    ]
