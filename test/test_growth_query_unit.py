"""持续增长榜查询层的单元测试（不连库）。

覆盖三件不需要数据库就能钉死的事：作用域解析（站点非法必须报错、不回退）、窗口边界
算术、排序白名单。窗口边界只差一天的错误在界面上完全看不出来——多回溯一天会让「近 7 天」
悄悄变成 8 天，而结果照样是一张合理的表。
"""
from __future__ import annotations

from datetime import date

import pytest

from app.table.keyword import growth_crud as crud
from app.table.keyword import growth_service as service
from app.table.keyword.growth_schemas import GrowthSearchRequest


def test_窗口含锚定日本身():
    """7 期窗口的左端是 d-6，不是 d-7。差这一天就变成 8 期、6 个环比变 7 个。"""
    assert crud.window_start(date(2026, 7, 15), 7) == date(2026, 7, 9)


def test_窗口跨月不特殊处理():
    assert crud.window_start(date(2026, 8, 3), 7) == date(2026, 7, 28)


def test_未传站点取默认站点():
    from config import settings

    assert service._resolve_site(None) == settings.default_site


def test_非法站点报错而不回退默认():
    """回退的表现是「用户以为在看 XX 站，实际看的是默认站」——两者在界面上分不开。"""
    with pytest.raises(service.GrowthScopeError):
        service._resolve_site("ZZ")


def test_英国是GB不是UK():
    with pytest.raises(service.GrowthScopeError):
        service._resolve_site("UK")


def test_未传日期取最新可用():
    dates = [date(2026, 7, 15), date(2026, 7, 14)]
    assert service._resolve_date(None, dates) == date(2026, 7, 15)


def test_一期数据都没有时日期为空而不是报错():
    """新站点接入初期就是这个状态，它是空结果不是错误。"""
    assert service._resolve_date(None, []) is None


def test_传了不在可选集里的日期报错():
    with pytest.raises(service.GrowthScopeError):
        service._resolve_date(date(2026, 1, 1), [date(2026, 7, 15)])


def test_排序白名单只有三列():
    """走势与窗口首期排名不可排序。每多一列可排序就多一种要验的执行计划。"""
    assert set(crud.SORTABLE_COLUMNS) == {"rank_gain", "min_growth_rate", "end_ranking"}


def test_默认按净增降序():
    req = GrowthSearchRequest()
    assert req.orderBy == "rank_gain"
    assert req.orderDir == "desc"


def test_走势聚合按关键词分组且保持期次顺序():
    rows = [
        (1, date(2026, 7, 9), 60000),
        (1, date(2026, 7, 10), 51000),
        (2, date(2026, 7, 10), 40000),
    ]

    ranks, dates = crud.shape_trends(rows)

    assert ranks == {1: [60000, 51000], 2: [40000]}
    assert dates == {1: [date(2026, 7, 9), date(2026, 7, 10)], 2: [date(2026, 7, 10)]}
