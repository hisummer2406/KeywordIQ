"""分段分页的算术。不连库——这是纯函数，也正是最该被独立测的一段。

在榜段与掉榜段各自有序且**永不交错**（掉榜恒在末尾），因此分页可以在应用层按边界
切分，不必 UNION ALL 之后再整体排序（那会强制物化两段全部行）。代价是这段边界算术
必须自己写对：算错的表现是某一行出现两次、或整行消失，而这两种在界面上都极难归因。

实测掉榜行占在榜行的 20–28%（见 design.md），所以这不是理论边界——用户翻得进去。
"""

from app.table.analysis.row_segments import plan_segments, remaining_for_dropped


class TestIncludeModes:
    def test_listed_only_never_touches_dropped(self):
        plan = plan_segments(offset=0, limit=50, listed_total=100, include="LISTED")
        assert plan.listed == (0, 50)
        assert plan.dropped is None

    def test_dropped_only_uses_offset_as_is(self):
        """仅掉榜时不减在榜数——那是「全部」模式下才需要的换算。"""
        plan = plan_segments(offset=20, limit=50, listed_total=100, include="DROPPED")
        assert plan.listed is None
        assert plan.dropped == (20, 50)


class TestAllModeBoundaries:
    """四种边界，逐一。这是 design.md 决策 5 点名要覆盖的部分。"""

    def test_page_entirely_within_listed(self):
        plan = plan_segments(offset=0, limit=50, listed_total=100, include="ALL")
        assert plan.listed == (0, 50)
        assert plan.dropped is None

    def test_page_ends_exactly_at_boundary(self):
        """末尾正好贴着分界线：仍然只查在榜段，不该多带一条掉榜行。"""
        plan = plan_segments(offset=50, limit=50, listed_total=100, include="ALL")
        assert plan.listed == (50, 50)
        assert plan.dropped is None

    def test_page_starts_exactly_at_boundary(self):
        """起点正好落在分界线上：整页来自掉榜段，且掉榜偏移归零。"""
        plan = plan_segments(offset=100, limit=50, listed_total=100, include="ALL")
        assert plan.listed is None
        assert plan.dropped == (0, 50)

    def test_page_straddles_boundary(self):
        """跨界：在榜段照样按整页条数问，掉榜段从头补齐差额。

        在榜段**不按 listed_total 裁剪**（这里不是 `(90, 10)`）。裁剪会让预存计数
        偏小时在榜行凭空消失——见 `TestStaleListedTotalDoesNotHideRows`。
        实际取回多少由查询说了算，掉榜段补的是「还差几条」。
        """
        plan = plan_segments(offset=90, limit=50, listed_total=100, include="ALL")
        assert plan.listed == (90, 50)
        assert plan.dropped == (0, 50)


class TestStaleListedTotalDoesNotHideRows:
    """预存的在榜计数偏小时，在榜行不能消失。

    这不是假想：`site_data_stats.row_count` 由导入收尾写入，而绕过 `run_import` 直接
    调 `import_report` 的路径（演示数据脚本、测试夹具）不会更新它。曾经的写法按这个数
    裁剪在榜段，于是「库里有 12 行、统计说 2 行」时用户只看得到 2 行——数据在、查询也
    没报错，就是没显示。这种故障没有任何一处会报警。
    """

    def test_first_page_asks_for_the_full_limit_regardless_of_stale_total(self):
        plan = plan_segments(offset=0, limit=100, listed_total=2, include="ALL")
        assert plan.listed == (0, 100), "在榜段必须按整页条数问，不能被预存数裁到 2 条"

    def test_dropped_fills_only_the_shortfall(self):
        """掉榜段给的是上限；真正要取几条由在榜段实际返回多少决定。"""
        plan = plan_segments(offset=0, limit=100, listed_total=2, include="ALL")
        assert plan.dropped == (0, 100)
        assert remaining_for_dropped(limit=100, listed_got=12) == 88


class TestDeepPages:
    def test_page_entirely_past_boundary(self):
        plan = plan_segments(offset=150, limit=50, listed_total=100, include="ALL")
        assert plan.listed is None
        assert plan.dropped == (50, 50)

    def test_no_listed_rows_at_all(self):
        """在榜段为空时整页来自掉榜段，偏移不做任何换算。"""
        plan = plan_segments(offset=0, limit=50, listed_total=0, include="ALL")
        assert plan.listed is None
        assert plan.dropped == (0, 50)


class TestPagesCoverEveryRowExactlyOnce:
    def test_sequential_pages_tile_the_combined_sequence(self):
        """把连续几页拼起来，必须恰好覆盖 [0, 在榜+掉榜) 且不重不漏。

        单看某一页的断言只能证明那一页对；真正要守的性质是「翻完所有页恰好读到每一行
        一次」，而重复与遗漏都只在页与页之间才显现。
        """
        listed_total, dropped_total, per_page = 7, 5, 3
        seen: list[tuple[str, int]] = []
        for page in range(4):  # 12 行 / 每页 3 行
            plan = plan_segments(
                offset=page * per_page, limit=per_page,
                listed_total=listed_total, include="ALL",
            )
            got = 0
            if plan.listed:
                off, lim = plan.listed
                rows = list(range(off, min(off + lim, listed_total)))
                seen += [("listed", i) for i in rows]
                got = len(rows)
            if plan.dropped:
                off, lim = plan.dropped
                # 服务层的做法：掉榜段只补「整页还差的那几条」。
                remaining = lim - got
                seen += [("dropped", i) for i in range(off, min(off + remaining, dropped_total))]

        expected = ([("listed", i) for i in range(listed_total)]
                    + [("dropped", i) for i in range(dropped_total)])
        assert seen == expected
