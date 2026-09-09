"""关键词排名趋势详情：纯函数单元测试（无需 PostgreSQL）。

覆盖锚定期解析（含跨视图映射与退化分支）、期望期次枚举、「全部可用历史」下界计算
（含"分区已被保留清理移出但 site_data_stats 残留"的防御场景）。见 design.md 决策 2/4。
"""
from datetime import date

from app.table.keyword.trend_service import (
    _expected_dates,
    _floor_for_range,
    _resolve_anchor,
)

D = date


def test_resolve_anchor_defaults_to_latest_when_on_not_given():
    dates = [D(2026, 7, 10), D(2026, 7, 9), D(2026, 7, 8)]  # 降序，与 available_dates_stmt 一致
    assert _resolve_anchor(dates, None) == D(2026, 7, 10)


def test_resolve_anchor_exact_match():
    dates = [D(2026, 7, 10), D(2026, 7, 9)]
    assert _resolve_anchor(dates, D(2026, 7, 9)) == D(2026, 7, 9)


def test_resolve_anchor_picks_nearest_leq_for_cross_view_mapping():
    """日视图的锚点切到周视图时，取 <= 该日期的最新一期，不是最新一期。"""
    week_dates = [D(2026, 7, 6), D(2026, 6, 29), D(2026, 6, 22)]
    assert _resolve_anchor(week_dates, D(2026, 7, 3)) == D(2026, 6, 29)


def test_resolve_anchor_degrades_to_global_latest_when_nothing_leq():
    """来源锚点早于目标视图的全部历史时，退化为目标视图的全局最新一期（design 决策 4）。"""
    week_dates = [D(2026, 7, 6), D(2026, 6, 29)]
    assert _resolve_anchor(week_dates, D(2020, 1, 1)) == D(2026, 7, 6)


def test_resolve_anchor_empty_available_dates_returns_none():
    assert _resolve_anchor([], D(2026, 7, 10)) is None
    assert _resolve_anchor([], None) is None


def test_expected_dates_day_step_inclusive_bounds():
    dates = _expected_dates(D(2026, 7, 10), 1, D(2026, 7, 8))
    assert dates == [D(2026, 7, 8), D(2026, 7, 9), D(2026, 7, 10)]


def test_expected_dates_week_step_aligns_to_anchor():
    """周步长从锚定期往回数，不是从下界往前数——对齐锚点才能复现导入侧"上一期"的定义。"""
    dates = _expected_dates(D(2026, 7, 20), 7, D(2026, 7, 1))
    assert dates == [D(2026, 7, 6), D(2026, 7, 13), D(2026, 7, 20)]


def test_expected_dates_single_point_when_floor_equals_anchor():
    assert _expected_dates(D(2026, 7, 10), 7, D(2026, 7, 10)) == [D(2026, 7, 10)]


def test_floor_for_range_recent30d_is_29_days_back():
    floor = _floor_for_range("RECENT_30D", D(2026, 7, 30), D(2026, 1, 1), [])
    assert floor == D(2026, 7, 1)  # 30 天窗口含锚定日本身


def test_floor_for_range_all_uses_earliest_available_within_retention():
    floor = _floor_for_range(
        "ALL", D(2026, 7, 30), D(2026, 5, 1),
        [D(2026, 7, 30), D(2026, 6, 1), D(2026, 5, 10)],
    )
    assert floor == D(2026, 5, 10)


def test_floor_for_range_all_ignores_stale_dates_before_retention_cutoff():
    """防御场景：`site_data_stats` 残留了已被保留清理 DROP 掉的月份的行（既有代码缺口，
    见 design.md 决策 2）。「全部可用历史」的下界 MUST NOT 采用那些残留日期，
    否则会声称能查到物理上已经不存在的数据。
    """
    floor = _floor_for_range(
        "ALL", D(2026, 7, 30), D(2026, 5, 1),
        [D(2026, 7, 30), D(2026, 6, 1), D(2026, 1, 1)],  # 1 月已超保留窗口
    )
    assert floor == D(2026, 6, 1)


def test_floor_for_range_all_falls_back_to_anchor_when_nothing_within_retention():
    floor = _floor_for_range("ALL", D(2026, 7, 30), D(2026, 5, 1), [D(2026, 1, 1)])
    assert floor == D(2026, 7, 30)


def test_floor_for_range_all_never_exceeds_anchor_when_anchor_itself_is_stale():
    """回归：锚点本身早于 cutoff（比如被选作锚点的是一条残留的过期 site_data_stats
    行），而窗口内还存在更晚近的合法日期时，下界 MUST NOT 晚于锚点——否则
    `_expected_dates` 会从锚点走向一个比它更晚的下界，一步都不执行，回传空列表，
    调用方对 `expected[0]` 取值会直接 IndexError。
    """
    anchor = D(2026, 1, 10)  # 早于 cutoff，本身是个 stale 锚点
    cutoff = D(2026, 5, 1)
    available = [anchor, D(2026, 6, 1), D(2026, 7, 1)]  # 后两个在保留窗口内、但晚于锚点
    floor = _floor_for_range("ALL", anchor, cutoff, available)
    assert floor <= anchor
    # 由此保证 `_expected_dates` 至少回传锚点自身这一个点，不会是空列表
    assert _expected_dates(anchor, 1, floor) == [anchor]
