"""ASIN/品牌悬浮历史 service 层的纯函数单元测试（无需 PostgreSQL）。

覆盖参数校验与站点规整（design.md 决策 6：page_size 超限拒绝而非截断）。
DB 相关行为（身份解析、跨关键词/站点隔离、重复占槽取最佳名次、分页与计数）见
test_topslot_history_integration.py。
"""
import pytest

from app.table.analysis.analysis_service import ScopeError
from app.table.analysis.topslot_history_service import (
    MAX_PAGE_SIZE,
    TopslotPageSizeExceededError,
    _resolve_site,
    _validate_page,
)


def test_resolve_site_normalizes_known_code():
    assert _resolve_site("us") == "US"


def test_resolve_site_rejects_unknown_code():
    with pytest.raises(ScopeError):
        _resolve_site("ZZ")


def test_validate_page_accepts_within_limit():
    assert _validate_page(2, MAX_PAGE_SIZE) == (2, MAX_PAGE_SIZE)


def test_validate_page_rejects_page_below_one():
    from app.common.errors import BadRequestError

    with pytest.raises(BadRequestError):
        _validate_page(0, 10)


def test_validate_page_rejects_page_size_below_one():
    from app.common.errors import BadRequestError

    with pytest.raises(BadRequestError):
        _validate_page(1, 0)


def test_validate_page_rejects_page_size_over_limit_not_truncate():
    """超限必须拒绝，而不是把 page_size 悄悄夹到上限——截断会让前端算出的页数与
    实际请求不一致（同 import_batch_crud.MAX_PER_PAGE 的既有范式）。
    """
    with pytest.raises(TopslotPageSizeExceededError):
        _validate_page(1, MAX_PAGE_SIZE + 1)
