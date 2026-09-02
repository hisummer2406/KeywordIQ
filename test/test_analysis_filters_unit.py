"""类目多选筛选的单元测试（无需 PostgreSQL）。

覆盖 redesign-analysis-filter-layout 任务 1.3：`categories` 的空值语义与个数上限。
真实 SQL（IN 条件与作用域边界）由 test_site_scoped_query_integration.py 覆盖。
"""
from datetime import date

import pytest
from pydantic import ValidationError

from app.table.analysis.analysis_crud import request_has_filters, search_stmt
from app.table.search.search_schemas import AnalysisSearchRequest


def _req(**kw) -> AnalysisSearchRequest:
    return AnalysisSearchRequest(site="US", view="DAY", **kw)


def test_non_empty_categories_counts_as_filter():
    """只勾了类目也算带筛选——总数必须走封顶计数，不能读切片总行数。"""
    assert request_has_filters(_req(categories=["Electronics"])) is True
    assert request_has_filters(_req(categories=["Electronics", "Pet Supplies"])) is True


def test_absent_categories_is_not_a_filter():
    assert request_has_filters(_req()) is False


def test_empty_categories_is_not_a_filter():
    """空集合视同不筛：否则「全不选」会走封顶计数，总数与不筛时不一致。"""
    assert request_has_filters(_req(categories=[])) is False


def test_categories_count_is_capped():
    """超过 100 个类目被拒。无上限的 IN 列表会让计划器放弃索引扫描。"""
    with pytest.raises(ValidationError):
        _req(categories=[f"cat-{i}" for i in range(101)])

    # 恰好 100 个仍然接受（上限是包含的）
    assert len(_req(categories=[f"cat-{i}" for i in range(100)]).categories) == 100


def test_category_exact_is_gone():
    """旧的单值字段已移除；误传不会被当成类目筛选（Pydantic 默认忽略未知字段）。"""
    req = AnalysisSearchRequest.model_validate(
        {"site": "US", "view": "DAY", "category_exact": "Electronics"}
    )
    assert not hasattr(req, "category_exact")
    assert request_has_filters(req) is False


# --------------------------------------------------------------------------
# ASIN 是精确码：等值匹配 + 规范化（issue #49）
# --------------------------------------------------------------------------


@pytest.mark.parametrize("given", ["b0slot00001", "  B0SLOT00001  ", "B0slot00001"])
def test_asin_is_normalised_to_upper(given):
    """大小写与首尾空白在请求模型里抹平。

    等值匹配区分大小写，而检索框的判定正则带 ``/i``、用户还常从别处粘贴带空格的串。
    规范化放在模型里而不是 CRUD 里，是为了让 ``/search`` 与 ``/export`` 共用同一份
    ——导出与列表用不同口径匹配同一个 ASIN，是那种没人会想到去查的错。
    """
    assert _req(asin=given).asin == "B0SLOT00001"


def test_blank_asin_is_dropped():
    """全空白视同没填。

    旧的 ``ILIKE '%%'`` 对空串匹配所有行，等值匹配则一行都不命中——空串若原样传下去，
    用户会得到一张空表且看不出是自己少填了什么。
    """
    assert _req(asin="   ").asin is None
    assert request_has_filters(_req(asin="   ")) is False


# --------------------------------------------------------------------------
# ASIN 三槽位任一命中 + 竞对新发现筛选（issue #56 P2）
#
# 这两条例外地断言 SQL 形状（而不是留给集成测试）：光靠 request_has_filters 或行为
# 断言看不出「只命中了 top1」这种退化，必须直接看编译出的 SQL 里有没有 top2/top3 那两列。
# --------------------------------------------------------------------------


def _compiled_sql(req: AnalysisSearchRequest) -> str:
    stmt = search_stmt("US", "DAY", date(2026, 7, 18), req, user_id=7)
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


def test_asin_filter_hits_all_three_product_slots():
    """此前只匹配 top1，一个实际排第二、第三名次的 ASIN 会查不到任何结果，且对用户不可见。"""
    sql = _compiled_sql(_req(asin="B0SLOT00001"))
    assert "top1_product_id" in sql
    assert "top2_product_id" in sql
    assert "top3_product_id" in sql


def test_competitor_new_filter_joins_watch_and_asin_and_hits_three_slots():
    """命中口径：前三商品槽位任一 ASIN 属于当前用户在当前站点关注的卖家**近期新发现**
    的 ASIN——不是"曾经上报过就算"（PR #66 复审 P1：`competitor_seller_asin` 不做
    下架删除，无时间限定的话这个开关会命中卖家有史以来上报过的全部商品，与字段名
    "新品"名实不符）。判据复用卖家清单「近 7 日新发现」同一个窗口常量与 SQL 形状。
    """
    sql = _compiled_sql(_req(competitor_new=True))
    assert "competitor_seller_asin" in sql
    assert "competitor_seller" in sql
    assert "user_seller_watch" in sql
    assert "top1_product_id" in sql
    assert "top2_product_id" in sql
    assert "top3_product_id" in sql
    # user_id 与 site 都作为常量条件出现（literal_binds 会把值编译进 SQL 文本）
    assert "user_seller_watch.user_id = 7" in sql
    assert "competitor_seller_asin.site = 'US'" in sql
    # 基线之后 + 窗口内两个时间条件都要出现，缺一个都会退化回"只要上报过就算"
    assert "first_seen_at >" in sql
    assert "baseline_at" in sql
    assert "interval" in sql


def test_competitor_new_alone_counts_as_a_filter():
    """决策 3 的回归：漏掉这一项，总数会静默走 site_data_stats 的预存值而不是精确计数，
    分页页码全部对不上，且两条路径都能正常跑完、不会有任何报错（issue #56 P2 design.md）。
    """
    assert request_has_filters(_req(competitor_new=True)) is True
    assert request_has_filters(_req(competitor_new=None)) is False


def test_competitor_new_false_is_not_a_filter():
    """`competitor_new` 只有两态（不像 `is_new` 三态），`False` 与 `None` 结果必须一样。

    `_apply_filters` 里是 `if req.competitor_new:`——`False` 不会被施加任何条件，
    如果这里裸传 `req.competitor_new` 会让 `False` 被 `v is not None` 误判成"带筛选"，
    从而错误地切到精确计数路径（PR #66 复审 P2）。
    """
    assert request_has_filters(_req(competitor_new=False)) is False
