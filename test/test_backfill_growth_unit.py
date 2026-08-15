"""回填脚本的期次枚举单元测试（不连库）。

枚举错了没有任何地方会报错：多算一期得到一张必然为空的榜单，少算一期则是界面上某天
「没有增长词」——两者都像是数据本身的样子。
"""
from datetime import date

from scripts.backfill_growth import enumerate_anchors

WINDOW = 7
# 2026-07-01 起连续 10 天
FULL = [date(2026, 7, d) for d in range(1, 11)]


def test_前几期窗口不完整被跳过():
    """7 期窗口下，最早能回填的锚定日是第 7 天——前 6 天回溯不出完整窗口。"""
    ready, incomplete = enumerate_anchors(FULL, WINDOW)

    assert ready == FULL[6:]
    assert incomplete == FULL[:6]


def test_中间缺一期让后续七期全部不可回填():
    available = [d for d in FULL if d != date(2026, 7, 5)]

    ready, incomplete = enumerate_anchors(available, WINDOW)

    # 07-05 落在 07-07..07-11 的窗口里；库里最晚是 07-10，故这一批全部不可回填
    assert ready == []
    assert date(2026, 7, 10) in incomplete


def test_区间只筛锚定日不影响窗口判定():
    """回填 07-10 一天，照样要求 07-04..07-10 七期都在——窗口不因区间收窄而放宽。"""
    ready, incomplete = enumerate_anchors(
        FULL, WINDOW, since=date(2026, 7, 10), until=date(2026, 7, 10)
    )

    assert ready == [date(2026, 7, 10)]
    assert incomplete == []


def test_区间早于可回填范围时为空():
    ready, incomplete = enumerate_anchors(FULL, WINDOW, until=date(2026, 7, 3))

    assert ready == []
    assert incomplete == FULL[:3]


def test_结果按期次升序():
    """乱序输入也要按升序回填：先算早的期次，中途失败时已完成的部分是连续的。"""
    ready, _ = enumerate_anchors(list(reversed(FULL)), WINDOW)

    assert ready == sorted(ready)


def test_没有数据时两组都为空():
    assert enumerate_anchors([], WINDOW) == ([], [])
