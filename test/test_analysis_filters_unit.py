"""类目多选筛选的单元测试（无需 PostgreSQL）。

覆盖 redesign-analysis-filter-layout 任务 1.3：`categories` 的空值语义与个数上限。
真实 SQL（IN 条件与作用域边界）由 test_site_scoped_query_integration.py 覆盖。
"""
import pytest
from pydantic import ValidationError

from app.table.analysis.analysis_crud import request_has_filters
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
