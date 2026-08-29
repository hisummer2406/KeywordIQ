"""词库查询层的单元测试（不连库）。

覆盖三类不需要数据库就能钉死的事：作用域解析、行整形的三态与 NULL 处理、盯盘语句的
形状。上限判定、幂等、跨用户隔离要真的写库才有意义，在
``test_keyword_library_integration.py`` 里（`TEST_DATABASE_URL` gate）。
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.table.keyword import library_crud as crud
from app.table.keyword import library_service as service
from app.table.keyword.library_schemas import (
    ItemsRequest,
    LibraryWatchRow,
    WatchRequest,
)

# ---------- 作用域解析 ----------

def test_非法站点报错而不回退默认():
    """回退的表现是「用户以为在看 XX 站，实际看的是另一个站」，两者在界面上分不开。"""
    with pytest.raises(service.LibraryScopeError):
        service._resolve_site("ZZ")


def test_英国是GB不是UK():
    with pytest.raises(service.LibraryScopeError):
        service._resolve_site("UK")


def test_未传日期取最新一期():
    dates = [date(2026, 7, 15), date(2026, 7, 14)]
    assert service._resolve_date(None, dates) == date(2026, 7, 15)


def test_站点一期数据都没有时日期为空而不是报错():
    assert service._resolve_date(None, []) is None


def test_传了不在可选集里的日期报错():
    with pytest.raises(service.LibraryScopeError):
        service._resolve_date(date(2026, 1, 1), [date(2026, 7, 15)])


def test_作用域错误码归关键词工具域不归分析域():
    """错误码按域划分：关键词工具的失败报成 analysis.* 会让按码筛日志把两条路径混在一起。"""
    assert service.LibraryScopeError.code.startswith("keyword.")


# ---------- 行整形：三态与 NULL ----------

class _FakeRow:
    """模拟 SQLAlchemy 行的 ``_mapping``。"""

    def __init__(self, mapping):
        self._mapping = mapping


def _row(segment, **extra):
    base = {
        "keyword_id": 1,
        "keyword": "wireless earbuds",
        "added_at": datetime(2026, 7, 1, tzinfo=UTC),
        "segment": segment,
        "last_seen_date": None,
        "last_ranking": None,
        "current_ranking": None,
        "top1_click_share": None,
        "is_new": None,
    }
    base.update(extra)
    return _FakeRow(base)


def test_三种状态各自翻译正确():
    rows = crud.shape_watch_rows([
        _row(0, current_ranking=12, top1_click_share=3.5, is_new=False),
        _row(1, last_seen_date=date(2026, 7, 10), last_ranking=800),
        _row(2),
    ])
    assert [r["status"] for r in rows] == ["LISTED", "DROPPED", "MISSING"]


def test_is_dropped由status派生():
    """两处各判一次的话，分叉的表现是「标着掉榜的行状态却是无数据」。"""
    rows = crud.shape_watch_rows([_row(0), _row(1), _row(2)])
    assert [r["is_dropped"] for r in rows] == [False, True, False]


def test_NULL列被剔除以便走schema默认值():
    """掉榜与无数据的行会把事实表那批列取回成 None，而 ``top1_click_share: float = 0``
    并不接受 None——不剔除就是一片校验错误。"""
    (row,) = crud.shape_watch_rows([_row(2)])
    assert "current_ranking" not in row
    assert "top1_click_share" not in row


def test_无数据行能通过行schema校验():
    """这条是上一条的下游：剔除之后，schema 的默认值必须把整行补齐。"""
    (row,) = crud.shape_watch_rows([_row(2)])
    item = LibraryWatchRow(**row)
    assert item.status == "MISSING"
    assert item.current_ranking is None
    assert item.top1_click_share == 0
    assert item.is_new is False


def test_在榜行保留事实值():
    (row,) = crud.shape_watch_rows([_row(0, current_ranking=12, top1_click_share=3.5)])
    item = LibraryWatchRow(**row)
    assert (item.status, item.current_ranking, item.top1_click_share) == ("LISTED", 12, 3.5)


# ---------- 盯盘语句的形状 ----------

def _watch_sql(view: str, order_by: str = "current_ranking", order_dir: str = "asc") -> str:
    return str(
        crud.watch_stmt(
            1, "US", view, date(2026, 7, 15),
            order_by=order_by, order_dir=order_dir, offset=0, limit=50,
        )
    )


def test_周视图用周报的分区列():
    """分区列名两表不同：照着日报的列名写周报查询会直接报 UndefinedColumn。"""
    assert "report_date_week" in _watch_sql("WEEK")
    assert "report_date_week" not in _watch_sql("DAY")


def test_事实表与掉榜表都是左连接():
    """行集合由条目决定，事实表只补数——内连接会让掉榜与无数据的词整行消失。"""
    sql = _watch_sql("DAY")
    assert sql.count("LEFT OUTER JOIN") >= 2
    assert "FROM analysis.keyword_group_item" in sql


def test_身份列取自条目表而不是事实表():
    """事实行可能整行为空，从那里取 keyword_id 会得到一行 NULL 的幽灵词。"""
    sql = _watch_sql("DAY")
    assert "keyword_group_item.keyword_id AS keyword_id" in sql


def test_段序恒在排序最前且不随升降序翻转():
    """掉榜与无数据没有可比较的排名，混进排序会让「最差的词」和「没数据的词」看起来
    是一回事。"""
    for order_dir in ("asc", "desc"):
        sql = _watch_sql("DAY", order_dir=order_dir)
        order_clause = sql.split("ORDER BY", 1)[1]
        assert order_clause.strip().startswith("CASE"), order_dir


def test_按加入时间排序时取条目表的列():
    assert "keyword_group_item.added_at" in _watch_sql("DAY", order_by="added_at").split("ORDER BY", 1)[1]


# ---------- 请求 schema ----------

def test_排序字段不在白名单直接报参数错误():
    """不静默回落到默认——回落会让用户以为按某列排了序，看到的却是另一种顺序。"""
    with pytest.raises(ValidationError):
        WatchRequest(group_id=1, orderBy="trend")


def test_走势与历史排名不可排序():
    from typing import get_args

    from app.table.keyword.library_schemas import LibrarySortField

    fields = set(get_args(LibrarySortField))
    assert not fields & {"trend", "trend_dates", "history"}


def test_单次加词条数有上限():
    """限的是单个请求体能有多大，与「一个分组能装多少词」是两回事。"""
    with pytest.raises(ValidationError):
        ItemsRequest(keyword_ids=list(range(501)))


def test_加词不接受空列表():
    with pytest.raises(ValidationError):
        ItemsRequest(keyword_ids=[])
