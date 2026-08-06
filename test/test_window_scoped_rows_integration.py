"""窗口化行集合的集成测试：两段分页、include 三态、历史参照点与掉榜行的趋势。

覆盖 tasks 3.10–3.13。行集合由两段构成——在榜（锚定期有数据）与掉榜（行窗口内出现过
但锚定期没有）——两段各发一条查询、在应用层按边界拼成一页。**边界算术是这条路径上唯一
没有数据库替你兜底的地方**：算错的表现是某一行出现两次、或整行消失，两种在界面上都极难
归因，所以这里把四种边界情形逐一钉住。

只在设置 TEST_DATABASE_URL 时运行（沿用 V2 集成测试的 gate 模式）。

    TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run pytest test/test_window_scoped_rows_integration.py -v
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.table.analysis import analysis_service as service
from app.table.search.search_schemas import AnalysisSearchRequest
from test.test_site_scoped_query_integration import (
    D2,
    D3,
    SITE,
    _async_url,
    _ensure_partitions_sync,
)

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)


@pytest.fixture()
async def db():
    if not _ensure_partitions_sync():
        pytest.skip("测试库未迁移，请先运行 alembic upgrade head")

    engine = create_async_engine(_async_url(TEST_URL), poolclass=NullPool)
    connection = await engine.connect()
    trans = await connection.begin()
    session = AsyncSession(bind=connection)
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await connection.close()
        await engine.dispose()


def _seed_segments(db, *, listed: int = 3, dropped: int = 2):
    """在 D3 造 ``listed`` 个在榜词与 ``dropped`` 个掉榜词。

    在榜词排名 1..listed，掉榜词的最后在榜排名从 100 起——**刻意让掉榜的名次数值更大**，
    这样"掉榜恒在末尾"若失效（比如误把两段并起来整体排序），断言会立刻发现顺序不对。

    ``site_data_stats`` 的两个计数与事实行一起写：生产上由导入收尾同时维护，只写事实行
    会让分页分界线读到 0，测出来的是一个现实中不存在的状态。
    """
    for i in range(1, listed + 1):
        kid = db.execute(
            text("INSERT INTO analysis.dim_keyword(site,keyword) VALUES (:s,:kw) "
                 "RETURNING keyword_id"),
            {"s": SITE, "kw": f"live-{i}"},
        ).scalar()
        db.execute(
            text("INSERT INTO analysis.fact_daily(site,report_date,keyword_id,current_ranking) "
                 "VALUES (:s,:d,:k,:r)"),
            {"s": SITE, "d": D3, "k": kid, "r": i},
        )
    for i in range(1, dropped + 1):
        kid = db.execute(
            text("INSERT INTO analysis.dim_keyword(site,keyword) VALUES (:s,:kw) "
                 "RETURNING keyword_id"),
            {"s": SITE, "kw": f"gone-{i}"},
        ).scalar()
        # 掉榜词在 D2 还在榜——它得有个"最后在榜"的期次
        db.execute(
            text("INSERT INTO analysis.fact_daily(site,report_date,keyword_id,current_ranking) "
                 "VALUES (:s,:d,:k,:r)"),
            {"s": SITE, "d": D2, "k": kid, "r": 100 + i},
        )
        db.execute(
            text("INSERT INTO analysis.fact_dropped"
                 "(site,period_type,anchor_date,keyword_id,last_seen_date,last_ranking) "
                 "VALUES (:s,'DAY',:a,:k,:l,:r)"),
            {"s": SITE, "a": D3, "k": kid, "l": D2, "r": 100 + i},
        )
    db.execute(
        text("INSERT INTO analysis.site_data_stats"
             "(site,period_type,report_date,row_count,dropped_count) "
             "VALUES (:s,'DAY',:d,:n,:m)"),
        {"s": SITE, "d": D3, "n": listed, "m": dropped},
    )
    db.execute(
        text("INSERT INTO analysis.site_data_stats"
             "(site,period_type,report_date,row_count,dropped_count) "
             "VALUES (:s,'DAY',:d,:n,0)"),
        {"s": SITE, "d": D2, "n": dropped},
    )


def _req(**kw) -> AnalysisSearchRequest:
    kw.setdefault("date", D3)
    return AnalysisSearchRequest(site=SITE, view="DAY", **kw)


async def _page(db, page: int, per: int, **kw):
    resp = await service.search(db, _req(page=page, perPage=per, **kw))
    return [r["keyword"] for r in resp.data], resp.total


# --------------------------------------------------------------------------
# 3.10 四种分页边界：无重复、无遗漏
# --------------------------------------------------------------------------


async def test_page_entirely_within_listed_segment(db):
    await db.run_sync(_seed_segments)          # 在榜 3 + 掉榜 2
    names, total = await _page(db, page=1, per=2)
    assert names == ["live-1", "live-2"]
    assert total == 5


async def test_page_straddles_the_segment_boundary(db):
    """整页正好跨在分界线上：前半来自在榜段，后半来自掉榜段。"""
    await db.run_sync(_seed_segments)
    names, _ = await _page(db, page=2, per=2)   # 第 3、4 条
    assert names == ["live-3", "gone-1"]


async def test_page_entirely_within_dropped_segment(db):
    """整页落在掉榜段：在榜段应被整条跳过，不发一次巨大 OFFSET 的空查询。"""
    await db.run_sync(_seed_segments)
    names, _ = await _page(db, page=3, per=2)   # 第 5 条
    assert names == ["gone-2"]


async def test_full_pagination_yields_every_row_exactly_once(db):
    """把所有页翻一遍：并集等于全集，且没有任何一行出现两次。

    这是边界算术唯一真正的验收条件——单看某一页"看着对"是不够的。
    """
    await db.run_sync(_seed_segments)
    seen: list[str] = []
    for page in (1, 2, 3):
        names, _ = await _page(db, page=page, per=2)
        seen += names
    assert seen == ["live-1", "live-2", "live-3", "gone-1", "gone-2"]
    assert len(seen) == len(set(seen)), "有行被返回了不止一次"


async def test_dropped_rows_stay_last_under_descending_order(db):
    """掉榜恒在在榜之后，不随排序方向翻转。

    掉榜行没有当期排名，参与当期字段的排序没有意义；若跟着翻转，用户按降序排时会先看到
    一批空当期排名的行，而它们并不是"排名最高"的。
    """
    await db.run_sync(_seed_segments)
    names, _ = await _page(db, page=1, per=5, orderBy="current_ranking", order="desc")
    assert names[-2:] == ["gone-1", "gone-2"], f"掉榜行没有留在末尾: {names}"


# --------------------------------------------------------------------------
# 3.11 筛选与 include 三态
# --------------------------------------------------------------------------


async def test_filters_exclude_dropped_rows_and_their_count(db):
    """任何筛选一生效，掉榜行全部消失，总数也不含它们。

    这不是优化，是「筛选一律以锚定期那一行为准」的必然结果——掉榜行在锚定期没有行可供
    匹配。界面侧必须明示，否则用户会以为筛掉的是"不满足条件的掉榜词"。
    """
    await db.run_sync(_seed_segments)
    names, total = await _page(db, page=1, per=10, keyword="live")
    assert all(not n.startswith("gone") for n in names)
    assert total == 3


async def test_include_listed_only(db):
    await db.run_sync(_seed_segments)
    names, total = await _page(db, page=1, per=10, include="LISTED")
    assert names == ["live-1", "live-2", "live-3"]
    assert total == 3


async def test_include_dropped_only(db):
    await db.run_sync(_seed_segments)
    names, total = await _page(db, page=1, per=10, include="DROPPED")
    assert names == ["gone-1", "gone-2"]
    assert total == 2


async def test_include_all_is_the_union(db):
    await db.run_sync(_seed_segments)
    names, total = await _page(db, page=1, per=10, include="ALL")
    assert names == ["live-1", "live-2", "live-3", "gone-1", "gone-2"]
    assert total == 5


# --------------------------------------------------------------------------
# 3.12 / 3.13 历史参照点、掉榜行的趋势、无掉榜记录的期次
# --------------------------------------------------------------------------


async def test_missing_reference_period_is_null_not_an_error(db):
    """参照期没有数据时该值为空——不报错，也不用相邻期顶替。

    顶替是最坏的做法：用户读到一个看似真实的数字，而它属于另一期。
    """
    await db.run_sync(_seed_segments)          # 只有 D2/D3 有数据，D1 之前是空的
    resp = await service.search(db, _req(page=1, perPage=1))
    row = resp.data[0]
    assert "history" in row
    # 「7 天前」（D3-7 = 2026-07-03）没有导入过，该点必须是 None
    assert row["history"][-1] is None


async def test_dropped_rows_carry_trend_and_last_seen(db):
    """掉榜行同样附带趋势，其最后一个点是它最后在榜的那一期。"""
    await db.run_sync(_seed_segments)
    resp = await service.search(db, _req(page=1, perPage=10, include="DROPPED"))
    row = resp.data[0]
    assert row["is_dropped"] is True
    assert row["last_seen_date"] == D2
    assert row["last_ranking"] == 101
    assert row["trend"], "掉榜行没有带趋势"
    # 当期字段一律为空，不用最后在榜期的值顶替
    assert row["current_ranking"] is None


async def test_period_without_dropped_records_returns_only_listed(db):
    """没有掉榜记录的历史期：只返回在榜行，不报错。

    D2 早于任何掉榜算定，fact_dropped 里没有它的行——查询要正常返回，而不是因为
    "查不到掉榜记录"而失败。
    """
    await db.run_sync(_seed_segments)
    resp = await service.search(db, _req(date=D2, page=1, perPage=10))
    assert resp.success
    assert resp.total == 2                      # D2 只有那两个后来掉榜的词
    assert all(r["is_dropped"] is False for r in resp.data)


async def test_scope_without_any_dropped_rows_at_all(db):
    """整个作用域一条掉榜记录都没有时，行集合退化为纯在榜段。"""
    await db.run_sync(lambda s: _seed_segments(s, listed=2, dropped=0))
    names, total = await _page(db, page=1, per=10)
    assert names == ["live-1", "live-2"]
    assert total == 2

