"""保留清理的单元测试——纯函数，不依赖真实 PostgreSQL。

守住两处"改错了也不会立刻报错"的逻辑：
- 保留下界 ``cutoff`` 的月份算术（跨年、边界月数），算错会多删或漏删整整一个月；
- 超期分区的挑选（``end <= cutoff``），把闭开区间的边界搞反会误删窗口内的当月数据。

真库层面的移出行为（DROP 分区、窗口内数据仍可查）见 test_retention_integration.py。
"""

from datetime import date
from unittest.mock import patch

import pytest

from app.table.analysis import retention_service
from app.table.analysis.partitioning import (
    MonthPartition,
    retention_cutoff,
    select_expired_partitions,
    subtract_months,
)


def test_default_archive_stat_refuses_when_oss_disabled():
    """未启用 OSS 必须抛错（由闸门判为 oss_unavailable），绝不能被当成校验通过。"""
    with patch.object(retention_service.settings, "OSS_ENABLED", False), patch.object(
        retention_service.oss_client, "stat_object"
    ) as stat:
        with pytest.raises(RuntimeError):
            retention_service._default_archive_stat("k/a.csv")
    stat.assert_not_called()


class TestSubtractMonths:
    def test_zero_returns_same_month_start(self):
        assert subtract_months(date(2026, 7, 15), 0) == date(2026, 7, 1)

    def test_within_year(self):
        assert subtract_months(date(2026, 7, 1), 2) == date(2026, 5, 1)

    def test_crosses_year_boundary(self):
        assert subtract_months(date(2026, 1, 10), 2) == date(2025, 11, 1)

    def test_crosses_multiple_years(self):
        assert subtract_months(date(2026, 3, 20), 15) == date(2024, 12, 1)


class TestRetentionCutoff:
    def test_three_months_keeps_current_plus_two(self):
        # 7 月、保留 3 个月 → 保留 5/6/7 月 → 下界为 5 月 1 日。
        assert retention_cutoff(date(2026, 7, 15), 3) == date(2026, 5, 1)

    def test_one_month_keeps_only_current(self):
        assert retention_cutoff(date(2026, 7, 31), 1) == date(2026, 7, 1)

    def test_cutoff_ignores_day_of_month(self):
        assert retention_cutoff(date(2026, 7, 1), 3) == retention_cutoff(date(2026, 7, 28), 3)

    def test_crosses_year(self):
        # 1 月、保留 3 个月 → 保留 2025-11、2025-12、2026-01。
        assert retention_cutoff(date(2026, 1, 5), 3) == date(2025, 11, 1)

    def test_rejects_non_positive_months(self):
        with pytest.raises(ValueError):
            retention_cutoff(date(2026, 7, 15), 0)


def _month(name: str, year: int, month: int) -> MonthPartition:
    start = date(year, month, 1)
    end = date(year + (month == 12), (month % 12) + 1, 1)
    return MonthPartition(name=name, start=start, end=end)


class TestSelectExpiredPartitions:
    def test_selects_only_partitions_before_cutoff(self):
        parts = [
            _month("fact_daily_2026_04", 2026, 4),
            _month("fact_daily_2026_05", 2026, 5),
            _month("fact_daily_2026_06", 2026, 6),
            _month("fact_daily_2026_07", 2026, 7),
        ]
        cutoff = retention_cutoff(date(2026, 7, 15), 3)  # 2026-05-01
        expired = select_expired_partitions(parts, cutoff)
        assert [p.name for p in expired] == ["fact_daily_2026_04"]

    def test_boundary_month_is_kept_not_dropped(self):
        # cutoff 恰为 5 月 1 日：5 月分区 [05-01,06-01) 的 end=06-01 > cutoff，必须保留。
        parts = [_month("fact_daily_2026_05", 2026, 5)]
        cutoff = date(2026, 5, 1)
        assert select_expired_partitions(parts, cutoff) == []

    def test_month_ending_exactly_at_cutoff_is_expired(self):
        # 4 月分区 [04-01,05-01) 的 end=05-01 == cutoff，视为超期。
        parts = [_month("fact_daily_2026_04", 2026, 4)]
        assert [p.name for p in select_expired_partitions(parts, date(2026, 5, 1))] == [
            "fact_daily_2026_04"
        ]

    def test_result_sorted_by_month_ascending(self):
        parts = [
            _month("fact_daily_2026_03", 2026, 3),
            _month("fact_daily_2026_01", 2026, 1),
            _month("fact_daily_2026_02", 2026, 2),
        ]
        expired = select_expired_partitions(parts, date(2026, 7, 1))
        assert [p.name for p in expired] == [
            "fact_daily_2026_01",
            "fact_daily_2026_02",
            "fact_daily_2026_03",
        ]

    def test_empty_input_returns_empty(self):
        assert select_expired_partitions([], date(2026, 5, 1)) == []
