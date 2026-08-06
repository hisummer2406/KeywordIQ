"""保留清理的数据库集成测试——验证移出行为在真实 PostgreSQL 上成立。

单元测试只能验证"挑哪些分区"，验证不了 DROP 一个月度分区是否真的连带删除其站点子
分区、窗口内数据是否仍可查、其他月份是否不受影响。这些只能在真库上验。

**默认跳过，永不误触生产。** 只在设置了 ``TEST_DATABASE_URL`` 时运行，且该库必须
是一次性的测试库（每个测试在事务里跑完即回滚）。

运行方式::

    DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run alembic upgrade head
    TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run pytest test/test_retention_integration.py -v
"""

from __future__ import annotations

import os
from datetime import date

import pytest
from sqlalchemy import create_engine, text

from app.table.analysis import partitioning

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)


@pytest.fixture()
def conn():
    """每个测试独占一个事务，结束即回滚——包括 DDL，测试之间零残留。"""
    engine = create_engine(TEST_URL)
    connection = engine.connect()
    trans = connection.begin()
    exists = connection.execute(text("SELECT to_regclass('analysis.fact_daily')")).scalar()
    if exists is None:
        trans.rollback()
        connection.close()
        engine.dispose()
        pytest.skip("测试库未迁移，请先运行 alembic upgrade head")
    try:
        yield connection
    finally:
        trans.rollback()
        connection.close()
        engine.dispose()


def _seed_month(conn, site: str, d: date, keyword: str, ranking: int) -> None:
    """建好分区并写入一行日报事实。"""
    partitioning.ensure_fact_partitions(conn, "fact_daily", site, d)
    kid = conn.execute(
        text("INSERT INTO analysis.dim_keyword(site,keyword) VALUES (:s,:k) RETURNING keyword_id"),
        {"s": site, "k": keyword},
    ).scalar()
    conn.execute(
        text("""INSERT INTO analysis.fact_daily(site,report_date,keyword_id,current_ranking)
                VALUES (:s,:d,:k,:r)"""),
        {"s": site, "d": d, "k": kid, "r": ranking},
    )


def _month_partition_names(conn) -> list[str]:
    return [p.name for p in partitioning.list_month_partitions(conn, "fact_daily")]


def _row_count(conn, d: date) -> int:
    return conn.execute(
        text("SELECT count(*) FROM analysis.fact_daily WHERE report_date = :d"), {"d": d}
    ).scalar()


# 本文件里各测试自己建的"窗口内"月份（相对固定的 cutoff 2026-05-01）。
_IN_WINDOW_MONTHS = frozenset(
    {"fact_daily_2026_05", "fact_daily_2026_06", "fact_daily_2026_07"}
)


def _assert_only_expired_dropped(dropped, cutoff: date) -> None:
    """被移出的每个分区都必须确实超期（``end <= cutoff``）。

    **断言写成"对全部分区的精确相等"是不成立的。** `keywordiq_test` 是全局共享的一份
    （见 CLAUDE.md「并行会话」一节），别的会话、或一次 verify-import 的真实导入，都会
    留下**已提交**的月分区——夹具的事务回滚清不掉它们，因为它们根本不是本测试建的。

    实际发生过：一次真实报表导入留下 `fact_daily_2026_01`，这两条测试随即以
    `['fact_daily_2026_01', 'fact_daily_2026_04'] != ['fact_daily_2026_04']` 失败。
    失败的是夹具"库里只有我建的东西"这个假设，被测逻辑完全正确，而 diff 看不出这一点。

    `end <= cutoff` 才是保留清理的真正契约：它对外来分区同样成立，因此在任何库状态下
    都是确定的。这比原来的写法更强——原来只覆盖本测试建的那几个月。
    """
    for p in dropped:
        assert p.end <= cutoff, f"{p.name} (end={p.end}) 未超期却被移出，cutoff={cutoff}"


def test_list_month_partitions_reports_bounds(conn):
    """枚举应从目录读出真实分区及其月度边界。"""
    partitioning.ensure_fact_partitions(conn, "fact_daily", "US", date(2026, 4, 10))
    parts = {p.name: p for p in partitioning.list_month_partitions(conn, "fact_daily")}
    assert "fact_daily_2026_04" in parts
    assert parts["fact_daily_2026_04"].start == date(2026, 4, 1)
    assert parts["fact_daily_2026_04"].end == date(2026, 5, 1)


def test_drop_expired_removes_only_out_of_window_months(conn):
    """保留最近三个月：4 月被移出，5/6/7 月保留且数据仍可查。"""
    _seed_month(conn, "US", date(2026, 4, 10), "kw apr", 1)
    _seed_month(conn, "US", date(2026, 5, 10), "kw may", 2)
    _seed_month(conn, "US", date(2026, 6, 10), "kw jun", 3)
    _seed_month(conn, "US", date(2026, 7, 10), "kw jul", 4)

    cutoff = partitioning.retention_cutoff(date(2026, 7, 15), 3)  # 2026-05-01
    dropped = partitioning.drop_partitions_older_than(conn, "fact_daily", cutoff)

    dropped_names = {p.name for p in dropped}
    assert "fact_daily_2026_04" in dropped_names
    assert dropped_names.isdisjoint(_IN_WINDOW_MONTHS)
    _assert_only_expired_dropped(dropped, cutoff)

    remaining = _month_partition_names(conn)
    assert "fact_daily_2026_04" not in remaining
    assert _IN_WINDOW_MONTHS <= set(remaining)
    # 窗口内数据仍可查，被移出月份查不到。
    assert _row_count(conn, date(2026, 4, 10)) == 0
    assert _row_count(conn, date(2026, 5, 10)) == 1
    assert _row_count(conn, date(2026, 7, 10)) == 1


def test_dropping_month_removes_its_site_subpartitions(conn):
    """DROP 月度一级分区应连带删除其站点子分区，不留孤儿。"""
    partitioning.ensure_fact_partitions(conn, "fact_daily", "US", date(2026, 4, 10))
    partitioning.ensure_fact_partitions(conn, "fact_daily", "GB", date(2026, 4, 10))
    partitioning.drop_month_partition(conn, "fact_daily", "fact_daily_2026_04")
    leftover = conn.execute(
        text("SELECT relname FROM pg_class WHERE relname LIKE 'fact_daily_2026_04%'")
    ).scalars().all()
    assert leftover == []


def test_in_window_months_are_never_dropped(conn):
    """窗口内的分区一个都不动。

    原名是 `test_in_window_only_is_noop`、断言 `dropped == []`——那句话只在"库里除了
    本测试建的东西什么都没有"时才成立，而这是个共享库（见 `_assert_only_expired_dropped`）。
    真正要守的性质是"窗口内的不许被碰"，它与库里还有什么无关。
    """
    _seed_month(conn, "US", date(2026, 6, 10), "kw jun", 1)
    _seed_month(conn, "US", date(2026, 7, 10), "kw jul", 2)
    cutoff = partitioning.retention_cutoff(date(2026, 7, 15), 3)
    dropped = partitioning.drop_partitions_older_than(conn, "fact_daily", cutoff)

    assert {p.name for p in dropped}.isdisjoint(_IN_WINDOW_MONTHS)
    _assert_only_expired_dropped(dropped, cutoff)
    assert _row_count(conn, date(2026, 6, 10)) == 1
    assert _row_count(conn, date(2026, 7, 10)) == 1


# --------------------------------------------------------------------------
# 掉榜表：一级月度分区，随保留窗口一同移出
# --------------------------------------------------------------------------


def _seed_dropped(conn, site: str, anchor: date, keyword: str, last_ranking: int) -> None:
    """建好掉榜表分区并写入一行掉榜记录。"""
    partitioning.ensure_fact_partitions(conn, "fact_dropped", site, anchor)
    kid = conn.execute(
        text("INSERT INTO analysis.dim_keyword(site,keyword) VALUES (:s,:k) RETURNING keyword_id"),
        {"s": site, "k": keyword},
    ).scalar()
    conn.execute(
        text("""INSERT INTO analysis.fact_dropped
                  (site,period_type,anchor_date,keyword_id,last_seen_date,last_ranking)
                VALUES (:s,'DAY',:a,:k,:seen,:r)"""),
        {"s": site, "a": anchor, "k": kid, "seen": anchor, "r": last_ranking},
    )


def _dropped_count(conn, site: str, anchor: date) -> int:
    """带作用域的计数。

    裸 `count(*)` 只在"库里除了我建的东西什么都没有"时成立，而这是个共享库——
    别的会话留下的已提交数据会让断言失败，且 diff 完全看不出是这个原因。
    """
    return conn.execute(
        text("SELECT count(*) FROM analysis.fact_dropped "
             "WHERE site = :s AND anchor_date = :a"),
        {"s": site, "a": anchor},
    ).scalar()


def test_dropped_table_partition_has_no_site_subpartition(conn):
    """掉榜表只建一级月度分区，不建站点子分区。

    建错成两级不会在建表时报错，只会在**写入时**报「no partition of relation found
    for row」——而那时错误信息指向的是导入，不是这里。
    """
    partitioning.ensure_fact_partitions(conn, "fact_dropped", "US", date(2026, 4, 10))
    # relkind 必须过滤：pg_class 里同名前缀的还有主键与索引（'i'），不筛会把
    # fact_dropped_2026_04_pkey 也算成一个分区。
    children = conn.execute(
        text("SELECT relname FROM pg_class "
             "WHERE relname LIKE 'fact_dropped_2026_04%' AND relkind IN ('r','p')")
    ).scalars().all()
    assert children == ["fact_dropped_2026_04"]


def test_dropped_rows_land_in_month_partition(conn):
    """一级分区必须真的能收下行——分区方案对不对，只有写一行才知道。"""
    _seed_dropped(conn, "US", date(2026, 4, 10), "kw dropped apr", 300)
    assert _dropped_count(conn, "US", date(2026, 4, 10)) == 1


def test_two_sites_share_one_month_partition(conn):
    """没有站点二级分区，两个站点的记录落在同一个月度分区里，各自仍可按站点查出。"""
    _seed_dropped(conn, "US", date(2026, 4, 10), "kw us", 100)
    _seed_dropped(conn, "GB", date(2026, 4, 10), "kw gb", 200)
    assert _dropped_count(conn, "US", date(2026, 4, 10)) == 1
    assert _dropped_count(conn, "GB", date(2026, 4, 10)) == 1


def test_retention_drops_expired_dropped_partitions(conn):
    """掉榜表适用同一个热窗口。

    不纳入保留清理的话它会成为一张只涨不掉的表：事实数据按月 DROP 滚出去了，指向
    它们的掉榜记录还在，且再也没有对应的事实行可供解释。
    """
    _seed_dropped(conn, "US", date(2026, 4, 10), "kw apr", 1)
    _seed_dropped(conn, "US", date(2026, 6, 10), "kw jun", 2)

    cutoff = partitioning.retention_cutoff(date(2026, 7, 15), 3)  # 2026-05-01
    dropped = partitioning.drop_partitions_older_than(conn, "fact_dropped", cutoff)

    assert "fact_dropped_2026_04" in {p.name for p in dropped}
    _assert_only_expired_dropped(dropped, cutoff)
    assert _dropped_count(conn, "US", date(2026, 4, 10)) == 0
    assert _dropped_count(conn, "US", date(2026, 6, 10)) == 1


def test_retention_service_covers_dropped_table(conn):
    """`run_retention` 遍历的是分区表注册表，掉榜表登记进去就自动被覆盖。

    守的是"注册表是唯一事实来源"这条——有人新增分区表却漏了保留清理时，这条会红。
    """
    assert "fact_dropped" in partitioning.PARTITIONED_TABLES
