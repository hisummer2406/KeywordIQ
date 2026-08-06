"""行集合的分段分页：在榜段与掉榜段如何切成一页。

行集合由两段构成——在榜（锚定期有数据）与掉榜（行窗口内出现过但锚定期没有）。两段
各自有序，且**永不交错**：掉榜行恒排在在榜行之后，不随排序方向翻转，因为它们没有
当期排名，参与当期字段的排序没有意义。

正因为不交错，分页可以在应用层按边界切分，两段各发一条带 OFFSET/LIMIT 的查询。
替代做法是 ``UNION ALL`` 之后整体排序，那会强制物化两段的全部行——在榜段本身就有
四十多万行，而这条路径每翻一页都要走一次。

代价是这段边界算术必须自己写对。写错的表现是某一行出现两次、或整行消失，两种在界面
上都极难归因。实测掉榜行占在榜行的 20–28%（见变更的 design.md），用户真的翻得进去，
不是理论边界。

本模块是纯函数，不碰数据库、不引入任何查询层依赖——它要能被单独测透。
"""

from __future__ import annotations

from dataclasses import dataclass

# 行集合维度。**不是筛选条件**：筛选一律以锚定期那一行为准，而掉榜行在锚定期没有行
# 可供匹配，把它做成筛选条件会与那条规则自相矛盾。
INCLUDE_ALL = "ALL"
INCLUDE_LISTED = "LISTED"
INCLUDE_DROPPED = "DROPPED"
VALID_INCLUDES = frozenset({INCLUDE_ALL, INCLUDE_LISTED, INCLUDE_DROPPED})


def wants_listed(include: str) -> bool:
    """本次请求要不要在榜段。"""
    return include in (INCLUDE_ALL, INCLUDE_LISTED)


def wants_dropped(include: str) -> bool:
    """本次请求要不要掉榜段。

    列表侧由 :func:`plan_segments` 顺带表达了这件事，导出侧没有分页、需要单独问一句
    ——两处都从这里取答案，免得一处改了另一处忘了（那会让导出与屏幕上的行集合不一致，
    而这正是导出最不该出的错）。
    """
    return include in (INCLUDE_ALL, INCLUDE_DROPPED)


@dataclass(frozen=True)
class SegmentPlan:
    """一页数据分别要向两段问什么。

    每项为 ``(offset, limit)``；``None`` 表示该段本页完全不参与，调用方据此**跳过整条
    查询**——这正是"前 N 页不触及掉榜记录"的实现方式。

    **两段的 limit 都是整页条数，不是各自应得的份额。** 在榜段实际返回多少由查询说了算，
    掉榜段随后只补「整页还差的那几条」（``remaining_for_dropped``）。这样安排是为了让
    在榜行的可见性不依赖 ``listed_total`` 准不准——见 :func:`plan_segments` 的说明。
    """

    listed: tuple[int, int] | None
    dropped: tuple[int, int] | None


def remaining_for_dropped(limit: int, listed_got: int) -> int:
    """掉榜段本页真正要取几条：整页条数减去在榜段实际返回的条数。"""
    return max(0, limit - listed_got)


def plan_segments(*, offset: int, limit: int, listed_total: int, include: str) -> SegmentPlan:
    """把一页 ``[offset, offset+limit)`` 映射到两段各自要问的偏移与条数。

    ``listed_total`` 是两段的分界线：无筛选时来自 ``site_data_stats`` 的预存值，带筛选时
    是锚定期切片的精确计数（此时掉榜段恒为空）。

    **在榜段不按 ``listed_total`` 裁剪。** 早先的写法是跨界时只向在榜段要
    ``listed_total - offset`` 条，那让在榜行的可见性直接押在预存计数上：计数偏小时，
    多出来的在榜行既不显示也不报错——库里有、查询也成功，就是没有那一行。而
    ``site_data_stats`` 恰恰有已知的漏更新路径（绕过 ``run_import`` 直接调
    ``import_report`` 的演示脚本与测试夹具）。改成按整页条数问、掉榜段补差额之后，
    这类偏差会被自动纠正：多出来的在榜行照常返回，掉榜段少补几条而已。

    预存计数仍被用来判断"这一页是不是已经整页越过了在榜段"——那种情况下在榜段查询只会
    返回空，而一个巨大的 OFFSET 在库里并不便宜，所以按分界线直接跳过。这是它剩下的
    唯一影响面：只在深翻页时起作用，且方向是少查一条不会有结果的查询。
    """
    if include == INCLUDE_LISTED:
        return SegmentPlan(listed=(offset, limit), dropped=None)
    if include == INCLUDE_DROPPED:
        # 偏移原样透传：减去 listed_total 是「全部」模式下的换算，这里没有前一段。
        return SegmentPlan(listed=None, dropped=(offset, limit))

    # INCLUDE_ALL
    if offset >= listed_total:
        return SegmentPlan(listed=None, dropped=(offset - listed_total, limit))

    dropped = (0, limit) if offset + limit > listed_total else None
    return SegmentPlan(listed=(offset, limit), dropped=dropped)
