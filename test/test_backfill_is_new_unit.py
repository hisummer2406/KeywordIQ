"""is_new 回填的期次切分单元测试（不连库）。"""
from datetime import date

from scripts.backfill_is_new import split_anchors

DAYS = [date(2026, 7, d) for d in range(1, 13)]


def test_回看窗口超出最早数据的期次被跳过():
    ready, skipped = split_anchors(DAYS, 7)

    # 最早 07-01；07-08 的窗口 [07-01, 07-07] 刚好完整
    assert ready == DAYS[7:]
    assert skipped == DAYS[:7]


def test_区间只筛锚定日不改变窗口判定():
    ready, skipped = split_anchors(DAYS, 7, since=date(2026, 7, 10), until=date(2026, 7, 11))

    assert ready == [date(2026, 7, 10), date(2026, 7, 11)]
    assert skipped == []


def test_无数据返回空():
    assert split_anchors([], 7) == ([], [])
