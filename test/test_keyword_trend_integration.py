"""关键词排名趋势详情（keyword-trend-detail）集成测试。

默认跳过，只在设置 TEST_DATABASE_URL 时运行（沿用 V2 集成测试的 gate 模式）。

用真实分区表验证五态分类，尤其是 design.md 决策 2 的防御场景：`site_data_stats`
残留了已超出保留窗口的行时，MUST NOT 被误判成「已导入但未收录」——那类数据物理上
已经不在事实表分区里，保留清理只 DROP 分区、从不清理 `site_data_stats`。

    TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run pytest test/test_keyword_trend_integration.py -v
"""
import os
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.table.analysis.partitioning import retention_cutoff
from app.table.keyword import trend_service as service
from config import settings

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)

SITE = "US"
TODAY = date.today()
CUTOFF = retention_cutoff(TODAY, settings.HOT_WINDOW_MONTHS)

# 近期期次：全部在保留窗口内。
D0, D1, D2, D3, D4 = (TODAY - timedelta(days=i) for i in range(5))
# 保留窗口之前的一个日期，只给 site_data_stats 写行、不写任何事实/掉榜行——
# 复现"分区已被保留清理 DROP、但统计表残留旧行"的既有缺口。
STALE_BEFORE_CUTOFF = CUTOFF - timedelta(days=10)

USER_ID = 1


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def _ensure_partitions_sync() -> bool:
    """建好本文件需要的月度分区（可能跨月，逐日建、幂等）。理由同
    test_site_scoped_query_integration.py 的同名函数：DDL 带参数，只能用同步连接。
    """
    from sqlalchemy import create_engine

    from app.table.analysis.partitioning import ensure_fact_partitions

    engine = create_engine(TEST_URL)
    try:
        with engine.begin() as conn:
            if conn.execute(text("SELECT to_regclass('analysis.fact_daily')")).scalar() is None:
                return False
            for d in (D0, D1, D2, D3, D4):
                ensure_fact_partitions(conn, "fact_daily", SITE, d)
                ensure_fact_partitions(conn, "fact_dropped", SITE, d)
        return True
    finally:
        engine.dispose()


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


def _seed(db):
    """K1：D0/D2 在榜（排名 100/300），D4 掉榜，D1 报表已导入但未收录，D3 报表未导入。

    另插入 STALE_BEFORE_CUTOFF 的 site_data_stats 行但不写任何事实/掉榜行——用来断言
    「已超出保留范围」优先于「已导入但未收录」。
    """
    k1 = (db.execute(
        text("INSERT INTO analysis.dim_keyword(site,keyword) VALUES (:s,'kw-trend') RETURNING keyword_id"),
        {"s": SITE},
    )).scalar()

    for d, rk in ((D0, 100), (D2, 300)):
        db.execute(
            text("INSERT INTO analysis.fact_daily(site,report_date,keyword_id,current_ranking) "
                 "VALUES (:s,:d,:k,:r)"),
            {"s": SITE, "d": d, "k": k1, "r": rk},
        )
    db.execute(
        text("INSERT INTO analysis.fact_dropped(site,period_type,anchor_date,keyword_id,"
             "last_seen_date,last_ranking) VALUES (:s,'DAY',:d,:k,:d,150)"),
        {"s": SITE, "d": D4, "k": k1},
    )
    # site_data_stats：D0/D1/D2/D4 报表已导入（D1 未收录该词），D3 未导入（不写行）。
    for d in (D0, D1, D2, D4, STALE_BEFORE_CUTOFF):
        db.execute(
            text("INSERT INTO analysis.site_data_stats(site,period_type,report_date,row_count) "
                 "VALUES (:s,'DAY',:d,1)"),
            {"s": SITE, "d": d},
        )
    db.flush()
    return k1


def _point(resp, d):
    return next(p for p in resp.points if p.date == d)


async def test_five_states_classified_correctly(db):
    k1 = await db.run_sync(_seed)
    resp = await service.get_keyword_trend(
        db, keyword_id=k1, site=SITE, view="DAY", range_="RECENT_30D", on=D0,
    )
    assert resp.on == D0
    assert _point(resp, D0).status == "RANKED"
    assert _point(resp, D0).ranking == 100
    assert _point(resp, D2).status == "RANKED"
    assert _point(resp, D2).ranking == 300
    assert _point(resp, D4).status == "DROPPED"
    assert _point(resp, D4).ranking is None
    assert _point(resp, D1).status == "NO_DATA"
    assert _point(resp, D3).status == "NOT_IMPORTED"


async def test_best_and_latest_available_ignore_non_ranked_points(db):
    k1 = await db.run_sync(_seed)
    resp = await service.get_keyword_trend(
        db, keyword_id=k1, site=SITE, view="DAY", range_="RECENT_30D", on=D0,
    )
    # 区间最好排名：100 与 300 中取更小者
    assert resp.best_ranking.ranking == 100
    assert resp.best_ranking.date == D0
    # 最近可用排名：D0 是最靠后的 RANKED 点（D0 本身就是锚定期）
    assert resp.latest_available.ranking == 100
    assert resp.latest_available.date == D0


async def test_latest_available_differs_from_anchor_when_anchor_not_ranked(db):
    """锚定期本身「已导入但未收录」时，最近可用排名 MUST NOT 顶替成锚定期的值
    ——它是另一天（更早）的数据。以 D1 为锚点：D0（比锚点更新）在窗口之外，
    D2（比锚点更早）在窗口内且是 RANKED，是唯一的候选。
    """
    k1 = await db.run_sync(_seed)
    resp = await service.get_keyword_trend(
        db, keyword_id=k1, site=SITE, view="DAY", range_="RECENT_30D", on=D1,
    )
    assert resp.on == D1
    assert _point(resp, D1).status == "NO_DATA"
    assert all(p.date != D0 for p in resp.points)  # D0 比锚点更新，不在窗口内
    assert resp.latest_available.date == D2  # 不是 D1
    assert resp.latest_available.ranking == 300


async def test_coverage_reflects_imported_periods_not_full_selected_range(db):
    k1 = await db.run_sync(_seed)
    resp = await service.get_keyword_trend(
        db, keyword_id=k1, site=SITE, view="DAY", range_="RECENT_30D", on=D0,
    )
    # covered 只含 D0/D1/D2/D4（有报表数据，无论是否收录该词）；D3 未导入，不计入覆盖范围。
    assert resp.coverage.start == D4
    assert resp.coverage.end == D0


async def test_out_of_retention_takes_priority_over_stale_site_data_stats(db):
    """核心回归（design.md 决策 2）：`site_data_stats` 里 STALE_BEFORE_CUTOFF 那行的报表
    早已随保留清理被 DROP，但统计表从未清理这行。以它为锚点查询 MUST NOT 得到
    「已导入但未收录」——那会让用户以为那份数据还在，只是没收这个词。
    """
    k1 = await db.run_sync(_seed)
    resp = await service.get_keyword_trend(
        db, keyword_id=k1, site=SITE, view="DAY", range_="RECENT_30D", on=STALE_BEFORE_CUTOFF,
    )
    assert resp.on == STALE_BEFORE_CUTOFF
    assert _point(resp, STALE_BEFORE_CUTOFF).status == "OUT_OF_RETENTION"
    # 整个近 30 天窗口都早于 cutoff，没有任何"已导入"的期次
    assert resp.coverage is None
    assert resp.best_ranking is None
    assert resp.latest_available is None


async def test_all_range_does_not_crash_when_anchor_itself_is_stale(db):
    """回归：`range=ALL` 且锚点本身早于保留下界时不得抛异常（曾因下界越过锚点，
    `_expected_dates` 回传空列表，`expected[0]` 直接 IndexError）。近期确实有更晚近
    的合法数据（D0/D2 等）存在，正是触发这个 bug 的必要条件——下界计算曾经会被
    这些更晚近的日期拉到锚点之后。
    """
    k1 = await db.run_sync(_seed)
    resp = await service.get_keyword_trend(
        db, keyword_id=k1, site=SITE, view="DAY", range_="ALL", on=STALE_BEFORE_CUTOFF,
    )
    assert resp.on == STALE_BEFORE_CUTOFF
    assert len(resp.points) == 1
    assert resp.points[0].status == "OUT_OF_RETENTION"
    assert resp.coverage is None


async def test_cross_site_keyword_id_rejected(db):
    k1 = await db.run_sync(_seed)
    with pytest.raises(service.KeywordTrendSiteMismatchError):
        await service.get_keyword_trend(
            db, keyword_id=k1, site="DE", view="DAY", range_="RECENT_30D", on=D0,
        )


async def test_unknown_keyword_id_returns_not_found(db):
    await db.run_sync(_seed)
    with pytest.raises(service.KeywordTrendNotFoundError):
        await service.get_keyword_trend(
            db, keyword_id=999_999_999, site=SITE, view="DAY", range_="RECENT_30D", on=D0,
        )


async def test_no_data_at_all_returns_null_anchor_without_error(db):
    """未 seed 的关键词不存在；改测"该站点该视图完全没有数据"要换一个没有 site_data_stats
    行的站点/视图组合——用 WEEK 视图（本文件从未写入任何周报数据）。
    """
    k1 = await db.run_sync(_seed)
    resp = await service.get_keyword_trend(
        db, keyword_id=k1, site=SITE, view="WEEK", range_="RECENT_30D", on=None,
    )
    assert resp.on is None
    assert resp.points == []
    assert resp.coverage is None
