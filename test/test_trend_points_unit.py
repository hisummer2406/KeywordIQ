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
        first_listed=False,
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
        first_listed=False,
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
        # 回溯范围取「趋势窗口」与「最远参照点」的大者，好让一次查询同时喂两者：
        #   日 趋势 6 天 vs 参照点 7 天 → 取 7 天
        #   周 趋势 21 天(4 周) vs 参照点 84 天(12 周) → 取 84 天
        ("DAY", "fact_daily", "2026-07-11"),
        ("WEEK", "fact_weekly", "2026-04-25"),
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


# --------------------------------------------------------------------------
# 历史排名：从趋势回溯结果里取，不新增查询
# --------------------------------------------------------------------------


def test_history_offsets_are_all_inside_the_fetch_window():
    """回溯范围必须盖得住最远那个参照点，否则历史排名列会永远为空。

    这是「历史排名不单独查」这个决定成立的前提：盖不住就只能再发一条查询，
    而那正是当初否掉的方案。
    """
    from app.table.analysis.analysis_crud import HISTORY_OFFSETS, fetch_lookback_days

    for view in ("DAY", "WEEK"):
        assert fetch_lookback_days(view) >= max(HISTORY_OFFSETS[view])


def test_day_history_offsets_are_yesterday_three_and_seven_days():
    from app.table.analysis.analysis_crud import HISTORY_OFFSETS

    assert HISTORY_OFFSETS["DAY"] == (1, 3, 7)


def test_week_history_offsets_are_one_four_and_twelve_weeks():
    """周视图的参照点以天计：上周 7 天、4 周前 28 天、12 周前 84 天。"""
    from app.table.analysis.analysis_crud import HISTORY_OFFSETS

    assert HISTORY_OFFSETS["WEEK"] == (7, 28, 84)


def test_trend_window_matches_the_row_window_in_both_views():
    """趋势小图的区间与行窗口一致：日 7 天、周 4 周。

    小图回答的是「这批行所在的这段时间里这个词怎么走」，与行集合同一个区间才对得上。
    它与历史排名列的参照点是两回事——周视图最远参照点是 12 周前，但那一点只进历史
    排名列，不进小图（否则 4 周的行窗口配一条 12 周的线，读的人无从判断线上哪一段
    对应表里这批行）。
    """
    from app.table.analysis.analysis_crud import _TREND_SPEC, fetch_lookback_days

    assert _TREND_SPEC["DAY"][2] == 6, "日趋势回溯 6 天 = 含锚定日共 7 点"
    assert _TREND_SPEC["WEEK"][2] == 21, "周趋势回溯 21 天 = 含锚定周共 4 点"

    # 回溯范围仍要够到最远参照点，否则历史排名列得另发一条查询
    assert fetch_lookback_days("DAY") == 7
    assert fetch_lookback_days("WEEK") == 84


def test_history_picks_the_exact_reference_periods():
    from app.table.analysis.analysis_crud import shape_history

    on = date(2026, 7, 18)
    rows = [
        (7, date(2026, 7, 11), 90),  # 7 天前
        (7, date(2026, 7, 15), 40),  # 3 天前
        (7, date(2026, 7, 16), 30),  # 窗口内但不是参照点
        (7, date(2026, 7, 17), 20),  # 昨天
        (7, on, 10),
    ]

    assert shape_history(rows, on, "DAY") == {7: [20, 40, 90]}


def test_missing_reference_period_becomes_none_not_zero():
    """参照期缺失（未导入或已被保留清理移出）要给 None。

    给 0 会被读成「排名第 0」，而 0 在这份数据里是「无上期」的既有编码——两个含义
    挤在同一个值上，界面无从区分。
    """
    from app.table.analysis.analysis_crud import shape_history

    on = date(2026, 7, 18)
    rows = [(7, date(2026, 7, 17), 20)]

    assert shape_history(rows, on, "DAY") == {7: [20, None, None]}


def test_trend_excludes_points_outside_the_render_window():
    """回溯查得比趋势要画的多一期（为了盖住 7 天前），多出来那期不能画进小图。

    不裁的话日视图会画出 8 个点，而规格说的是 7 个。
    """
    from app.table.analysis.analysis_crud import shape_trends

    on = date(2026, 7, 18)
    rows = [
        (7, date(2026, 7, 11), 90),  # d-7：只供历史排名，不进趋势
        (7, date(2026, 7, 12), 80),  # d-6：趋势的第一个点
        (7, on, 10),
    ]

    assert shape_trends(rows, on=on, view="DAY") == {7: [80, 10]}


@pytest.mark.parametrize(
    "view, outside, inside",
    [
        # 日：回溯 7 天盖住 d-7 参照点，趋势只画 [d-6, d] 共 7 点
        ("DAY", date(2026, 7, 11), date(2026, 7, 12)),
        # 周：回溯 84 天盖住 w-12 参照点，趋势只画 [w-3, w] 共 4 点
        ("WEEK", date(2026, 4, 25), date(2026, 6, 27)),
    ],
)
def test_fetch_trends_trims_to_the_render_window(view, outside, inside):
    """导出走的 ``fetch_trends`` 必须与列表页用同一个裁剪窗口。

    不裁的话导出的走势会比界面多出参照点那几期（日 8 点 vs 7 点、周 13 点 vs 4 点），
    而导出的契约是「用户当下看到的那份数据」——两边的点数不一样，拿 CSV core 对界面
    就会发现对不上，且没有任何地方会报错。
    """
    from app.table.analysis.analysis_crud import fetch_trends

    on = date(2026, 7, 18)
    rows = [(7, outside, 90), (7, inside, 80), (7, on, 10)]

    class _FakeResult:
        def all(self):
            return rows

    class _FakeSession:
        def execute(self, _stmt):
            return _FakeResult()

    assert fetch_trends(_FakeSession(), "US", [7], on, view) == {7: [80, 10]}
