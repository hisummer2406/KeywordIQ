"""持续增长榜查询层的集成测试（真库 + asyncpg）。

必须打异步链路：查询侧走 ``AsyncSession``/asyncpg，而 asyncpg 与 psycopg2 在
Numeric、日期、NULL 上的行为有差异——用同步会话验异步实现等于没验（本仓库既有集成测试
的注释里已经写明这条）。

默认跳过，只在设置 ``TEST_DATABASE_URL`` 时运行。每个测试独占事务、结束回滚。

    TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run pytest test/test_growth_query_integration.py -v
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.table.analysis.partitioning import ensure_fact_partitions
from app.table.keyword import growth_service as service
from app.table.keyword.growth_schemas import GrowthSearchRequest
from config import settings
from test.test_site_scoped_query_integration import _async_url

TEST_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="设置 TEST_DATABASE_URL 运行增长榜查询集成测试")

@pytest.fixture(autouse=True)
def _pinned_growth_thresholds(monkeypatch):
    """把口径阈值钉在默认值上，不随本机 .env 漂移。

    这些用例的全部意义是「每个阈值的两侧各钉一个」——夹具造的数据精确卡在 9.9% 与
    10% 之间。而 `load_growth` 与作用域端点都在调用时读 `settings.GROWTH_*`，于是本机
    .env 只要覆盖了阈值（开发机上确实有人把 GROWTH_MIN_DAILY_RATE 调成 0.05 做过实验），
    这些用例就稳定失败，而 CI 因为没有 .env 一直是绿的（issue #41）。

    失败信息完全指不到成因：看到的是"这个词不该在榜上却在榜上"，不会想到是环境变量。
    """
    monkeypatch.setattr(settings, "GROWTH_WINDOW_DAYS", 7)
    monkeypatch.setattr(settings, "GROWTH_MIN_RANK_GAIN", 10000)
    monkeypatch.setattr(settings, "GROWTH_MIN_DAILY_RATE", 0.10)


SITE = "US"
ANCHOR = date(2026, 7, 15)
WINDOW = [ANCHOR - timedelta(days=6 - i) for i in range(7)]


@pytest.fixture()
async def db():
    # 分区 DDL 走同步连接并提交：DDL 带绑定参数，PostgreSQL 的扩展查询协议不允许，
    # asyncpg 会直接报错（psycopg2 在客户端插值才可行）。
    sync_engine = create_engine(TEST_URL)
    try:
        with sync_engine.begin() as conn:
            if conn.execute(text("SELECT to_regclass('analysis.fact_growth')")).scalar() is None:
                pytest.skip("测试库未迁移到含 fact_growth 的版本")
            ensure_fact_partitions(conn, "fact_daily", SITE, WINDOW[0])
            ensure_fact_partitions(conn, "fact_daily", SITE, ANCHOR)
            ensure_fact_partitions(conn, "fact_growth", SITE, ANCHOR)
    finally:
        sync_engine.dispose()

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


async def _seed_stats(db: AsyncSession, dates=WINDOW):
    """写 site_data_stats——「哪些日期有数据」的唯一来源，日期选择器与缺期都读它。"""
    for d in dates:
        await db.execute(
            text("INSERT INTO analysis.site_data_stats"
                 "(site,period_type,report_date,row_count,dropped_count) "
                 "VALUES (:s,'DAY',:d,1,0)"),
            {"s": SITE, "d": d},
        )


async def _seed_board(db: AsyncSession, keyword: str, *, gain: int, min_rate: float,
                      end_ranking: int = 1000):
    """造一条算定好的榜单行 + 窗口内七期事实行（供走势查询取用）。"""
    kid = (
        await db.execute(
            text("INSERT INTO analysis.dim_keyword(site,keyword) VALUES (:s,:kw) "
                 "RETURNING keyword_id"),
            {"s": SITE, "kw": keyword},
        )
    ).scalar()
    start = end_ranking + gain
    for i, d in enumerate(WINDOW):
        await db.execute(
            text("INSERT INTO analysis.fact_daily(site,report_date,keyword_id,current_ranking) "
                 "VALUES (:s,:d,:k,:r)"),
            {"s": SITE, "d": d, "k": kid,
             "r": start - round(gain * i / (len(WINDOW) - 1))},
        )
    await db.execute(
        text("INSERT INTO analysis.fact_growth"
             "(site,anchor_date,keyword_id,start_ranking,end_ranking,rank_gain,min_growth_rate) "
             "VALUES (:s,:a,:k,:sr,:er,:g,:mr)"),
        {"s": SITE, "a": ANCHOR, "k": kid, "sr": start, "er": end_ranking,
         "g": gain, "mr": min_rate},
    )
    return kid


async def test_列表走ProTable契约并回显作用域(db):
    await _seed_stats(db)
    await _seed_board(db, "kw-a", gain=40000, min_rate=0.12)

    resp = await service.search(db, GrowthSearchRequest(site=SITE, date=ANCHOR))

    assert resp.success is True
    assert resp.total == 1
    assert resp.site == SITE and resp.date == ANCHOR
    assert resp.window_days == 7
    assert resp.data[0].keyword == "kw-a"
    assert resp.data[0].rank_gain == 40000


async def test_走势点数恰为窗口期数(db):
    """多一个点少一个点都不会报错——只会画出一张「近 8 天」的图而无人察觉。"""
    await _seed_stats(db)
    await _seed_board(db, "kw-a", gain=40000, min_rate=0.12)

    resp = await service.search(db, GrowthSearchRequest(site=SITE, date=ANCHOR))
    row = resp.data[0]

    assert len(row.trend) == 7
    assert row.trend_dates == WINDOW
    assert row.trend[0] == row.start_ranking and row.trend[-1] == row.end_ranking


async def test_默认按净增降序且排序方向可翻转(db):
    await _seed_stats(db)
    await _seed_board(db, "small", gain=15000, min_rate=0.11)
    await _seed_board(db, "big", gain=40000, min_rate=0.12)

    desc = await service.search(db, GrowthSearchRequest(site=SITE, date=ANCHOR))
    asc = await service.search(
        db, GrowthSearchRequest(site=SITE, date=ANCHOR, orderDir="asc")
    )

    assert [r.keyword for r in desc.data] == ["big", "small"]
    assert [r.keyword for r in asc.data] == ["small", "big"]


async def test_按最低环比排序(db):
    await _seed_stats(db)
    await _seed_board(db, "steady", gain=15000, min_rate=0.30)
    await _seed_board(db, "barely", gain=40000, min_rate=0.10)

    resp = await service.search(
        db, GrowthSearchRequest(site=SITE, date=ANCHOR, orderBy="min_growth_rate")
    )

    assert [r.keyword for r in resp.data] == ["steady", "barely"]


async def test_分页不重不漏(db):
    await _seed_stats(db)
    for i in range(5):
        await _seed_board(db, f"kw-{i}", gain=10001 + i * 1000, min_rate=0.11)

    p1 = await service.search(db, GrowthSearchRequest(site=SITE, date=ANCHOR, perPage=2, page=1))
    p2 = await service.search(db, GrowthSearchRequest(site=SITE, date=ANCHOR, perPage=2, page=2))
    p3 = await service.search(db, GrowthSearchRequest(site=SITE, date=ANCHOR, perPage=2, page=3))

    keywords = [r.keyword for r in (*p1.data, *p2.data, *p3.data)]
    assert len(keywords) == len(set(keywords)) == 5
    assert p1.total == p2.total == 5


async def test_缺期随响应回传(db):
    """榜单为空时用户必须能看出是「缺数据」而不是「今天没有增长词」。"""
    present = [d for d in WINDOW if d not in (WINDOW[2], WINDOW[4])]
    await _seed_stats(db, present)

    resp = await service.search(db, GrowthSearchRequest(site=SITE, date=ANCHOR))

    assert resp.missing_dates == [WINDOW[2], WINDOW[4]]
    assert resp.data == []


async def test_窗口完整时缺期列表为空(db):
    await _seed_stats(db)
    await _seed_board(db, "kw-a", gain=40000, min_rate=0.12)

    resp = await service.search(db, GrowthSearchRequest(site=SITE, date=ANCHOR))

    assert resp.missing_dates == []


async def test_作用域端点回传口径(db, monkeypatch):
    """端点如实回传**当前配置**的三个阈值。

    故意钉成非默认值再断言：若照默认值断言字面量 7 / 10000 / 0.10，端点把这三个数写死
    也照样绿，而这条用例要守的恰恰是「不写死」——界面据此拼口径说明条，两份副本一旦分叉，
    用户看到的口径就不是后端实际用的那个，且没有任何地方会报错。
    """
    await _seed_stats(db)
    monkeypatch.setattr(settings, "GROWTH_WINDOW_DAYS", 5)
    monkeypatch.setattr(settings, "GROWTH_MIN_RANK_GAIN", 12345)
    monkeypatch.setattr(settings, "GROWTH_MIN_DAILY_RATE", 0.07)

    scope = await service.get_scope(db, SITE)

    assert scope.latest_date == ANCHOR
    assert scope.available_dates[0] == ANCHOR
    assert (scope.window_days, scope.min_rank_gain, scope.min_daily_rate) == (5, 12345, 0.07)


async def test_非法站点报错不回退(db):
    with pytest.raises(service.GrowthScopeError):
        await service.search(db, GrowthSearchRequest(site="ZZ"))


async def test_不在可选集里的日期报错(db):
    await _seed_stats(db)

    with pytest.raises(service.GrowthScopeError):
        await service.search(db, GrowthSearchRequest(site=SITE, date=date(2026, 1, 1)))
