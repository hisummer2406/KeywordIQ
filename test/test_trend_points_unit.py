from datetime import date

import pytest

from app.table.analysis.analysis_crud import (
    shape_trend_dates,
    shape_trends,
    trends_stmt,
)
from app.table.search.search_schemas import DayRowItem, WeekRowItem


def test_trend_values_and_dates_keep_the_same_order():
    rows = [
        (7, date(2026, 7, 17), 20),
        (7, date(2026, 7, 18), 10),
    ]

    assert shape_trends(rows) == {7: [20, 10]}
    assert shape_trend_dates(rows) == {7: [date(2026, 7, 17), date(2026, 7, 18)]}


def test_day_row_serializes_trend_dates():
    item = DayRowItem(
        keyword_id=7,
        keyword="coffee maker",
        current_ranking=10,
        previous_ranking=20,
        ranking_change=-10,
        is_new=False,
        trend=[20, 10],
        trend_dates=[date(2026, 7, 17), date(2026, 7, 18)],
    )

    assert item.model_dump(mode="json")["trend_dates"] == ["2026-07-17", "2026-07-18"]


def test_week_row_also_carries_trend():
    """趋势是两视图共有字段，不是日视图独有。"""
    item = WeekRowItem(
        keyword_id=7,
        keyword="coffee maker",
        current_ranking=10,
        previous_ranking=20,
        ranking_change=-10,
        is_new=False,
        trend=[30, 20, 10],
        trend_dates=[date(2026, 6, 27), date(2026, 7, 4), date(2026, 7, 11)],
    )

    dumped = item.model_dump(mode="json")
    assert dumped["trend"] == [30, 20, 10]
    assert dumped["trend_dates"][-1] == "2026-07-11"


# --------------------------------------------------------------------------
# 回溯窗口：两个视图查不同的事实表、回溯不同的天数，其余 SQL 一字不差
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "view, table, earliest",
    [
        ("DAY", "fact_daily", "2026-07-12"),  # 7-18 往前 6 天
        ("WEEK", "fact_weekly", "2026-06-06"),  # 7-18 往前 42 天
    ],
)
def test_trends_stmt_targets_the_view_s_fact_table_and_window(view, table, earliest):
    sql = str(trends_stmt("US", [1, 2], date(2026, 7, 18), view).compile(
        compile_kwargs={"literal_binds": True}
    ))

    assert f"analysis.{table}" in sql
    assert earliest in sql
    assert "2026-07-18" in sql


def test_trends_stmt_rejects_unknown_view():
    """视图码是内部枚举，拿不到就该炸——静默回落到日报会让周视图显示错误的数据。"""
    with pytest.raises(KeyError):
        trends_stmt("US", [1], date(2026, 7, 18), "MONTH")
