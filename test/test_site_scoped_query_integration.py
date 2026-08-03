"""站点作用域查询（site-scoped-query）集成测试。

默认跳过，只在设置 TEST_DATABASE_URL 时运行（沿用 V2 集成测试的 gate 模式）。测试库必须
已迁移（alembic upgrade head）。每个测试独占一个事务，结束即回滚，零残留。

用受控小样本验证 A 期（作用域/筛选/排序/总数）与 B 期（7 天走势/当周排名变化）——合成数据
在小规模下关键词跨天不重叠，无法测走势，故这里手工构造跨天数据。

    TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run pytest test/test_site_scoped_query_integration.py -v
"""
import os
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.table.analysis import analysis_service as service
from app.table.search.search_schemas import AnalysisSearchRequest

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)

SITE = "US"
D1, D2, D3 = date(2026, 7, 8), date(2026, 7, 9), date(2026, 7, 10)  # 同 ISO 周
WEEK = date(2026, 7, 10)  # 与 D1..D3 同 ISO 周


def _async_url(url: str) -> str:
    """把同步测试库 URL 换成 asyncpg 驱动。

    查询路径已异步化，测试必须打同一条链路（asyncpg 与 psycopg2 在数值/日期/NULL
    上的行为有差异，用同步会话验异步实现等于没验）。
    """
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def _ensure_partitions_sync() -> bool:
    """用一次性同步连接建好本文件所需的分区并提交。

    分区**不能**在异步会话上建：``ensure_fact_partitions`` 的 DDL 带绑定参数，
    而 PostgreSQL 的扩展查询协议不允许 DDL 带参数，asyncpg 会直接报错
    （psycopg2 因为在客户端插值才可行）。这与 COPY 一样，是导入路径必须同步的
    硬约束之一。

    提交而非回滚是刻意的：分区是幂等 DDL，本就该持久存在；被回滚的只有数据行。
    返回 False 表示测试库尚未迁移。
    """
    from sqlalchemy import create_engine

    from app.table.analysis.partitioning import ensure_fact_partitions

    engine = create_engine(TEST_URL)
    try:
        with engine.begin() as conn:
            if conn.execute(text("SELECT to_regclass('analysis.fact_daily')")).scalar() is None:
                return False
            ensure_fact_partitions(conn, "fact_daily", SITE, D1)
            ensure_fact_partitions(conn, "fact_weekly", SITE, WEEK)
        return True
    finally:
        engine.dispose()


@pytest.fixture()
async def db():
    if not _ensure_partitions_sync():
        pytest.skip("测试库未迁移，请先运行 alembic upgrade head")

    # NullPool：每次新连接、用完即关，规避连接归还后仍持有咨询锁导致的抢锁时序问题。
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
    """受控数据：K1 跨 D1/D2/D3（排名 5→3→1）+ 同周周报（变化 7）；K2 只在 D3（排名 9）。

    **保持同步**：导出集成测试直接以同步会话调用它；异步测试经 ``run_sync`` 桥接。
    分区由 fixture 用同步连接预先建好（asyncpg 建不了带参数的 DDL），这里只写数据行。
    """
    k1 = (db.execute(
        text("INSERT INTO analysis.dim_keyword(site,keyword) VALUES (:s,'kw-one') RETURNING keyword_id"),
        {"s": SITE},
    )).scalar()
    k2 = (db.execute(
        text("INSERT INTO analysis.dim_keyword(site,keyword) VALUES (:s,'kw-two') RETURNING keyword_id"),
        {"s": SITE},
    )).scalar()

    for d, rk in [(D1, 5), (D2, 3), (D3, 1)]:
        db.execute(
            text("INSERT INTO analysis.fact_daily(site,report_date,keyword_id,current_ranking) "
                 "VALUES (:s,:d,:k,:r)"),
            {"s": SITE, "d": d, "k": k1, "r": rk},
        )
    db.execute(
        text("INSERT INTO analysis.fact_daily(site,report_date,keyword_id,current_ranking) "
             "VALUES (:s,:d,:k,9)"),
        {"s": SITE, "d": D3, "k": k2},
    )
    db.execute(
        text("INSERT INTO analysis.fact_weekly(site,report_date_week,keyword_id,current_ranking,ranking_change) "
             "VALUES (:s,:w,:k,1,7)"),
        {"s": SITE, "w": WEEK, "k": k1},
    )
    # site_data_stats：日期选择器与总数来源
    for d, n in [(D1, 1), (D2, 1), (D3, 2)]:
        db.execute(
            text("INSERT INTO analysis.site_data_stats(site,period_type,report_date,row_count) "
                 "VALUES (:s,'DAY',:d,:n)"),
            {"s": SITE, "d": d, "n": n},
        )
    db.execute(
        text("INSERT INTO analysis.site_data_stats(site,period_type,report_date,row_count) "
             "VALUES (:s,'WEEK',:w,1)"),
        {"s": SITE, "w": WEEK},
    )
    db.flush()
    return k1, k2


async def test_scope_lists_dates_descending(db):
    await db.run_sync(_seed)
    scope = await service.get_scope(db, SITE, "DAY")
    assert scope.available_dates == [D3, D2, D1]
    assert scope.latest_date == D3


async def test_search_scoped_total_and_rows(db):
    await db.run_sync(_seed)
    resp = await service.search(db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3))
    assert resp.success and resp.date == D3
    assert resp.total == 2  # 来自 site_data_stats
    rankings = [r["current_ranking"] for r in resp.data]
    assert rankings == [1, 9]  # 默认按 current_ranking 升序


async def test_day_view_trend_and_week_change(db):
    k1, k2 = await db.run_sync(_seed)
    resp = await service.search(db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3))
    rows = {r["keyword_id"]: r for r in resp.data}
    assert rows[k1]["trend"] == [5, 3, 1]      # 按日期回溯
    assert rows[k1]["week_change"] == 7        # 同 ISO 周周报映射
    assert rows[k2]["trend"] == [9]            # 只有 D3
    assert rows[k2]["week_change"] is None     # 仅日报，无对应周报


async def test_trend_is_as_of_selected_date(db):
    k1, _ = await db.run_sync(_seed)
    resp = await service.search(db, AnalysisSearchRequest(site=SITE, view="DAY", date=D2))
    rows = {r["keyword_id"]: r for r in resp.data}
    assert rows[k1]["trend"] == [5, 3]  # 截至 D2，不含 D3


async def test_sort_whitelist_rejects_trend(db):
    await db.run_sync(_seed)
    # 非法排序字段 trend → 回落默认 current_ranking 升序
    resp = await service.search(
        db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3, orderBy="trend")
    )
    assert [r["current_ranking"] for r in resp.data] == [1, 9]


async def test_invalid_date_raises_scope_error(db):
    await db.run_sync(_seed)
    with pytest.raises(service.ScopeError):
        await service.search(db, AnalysisSearchRequest(site=SITE, view="DAY", date=date(2020, 1, 1)))


async def test_week_view_excludes_trend_fields(db):
    await db.run_sync(_seed)
    resp = await service.search(db, AnalysisSearchRequest(site=SITE, view="WEEK", date=WEEK))
    assert resp.total == 1
    assert resp.data and "trend" not in resp.data[0] and "week_change" not in resp.data[0]


async def test_scope_boundary_excludes_other_dates(db):
    """作用域边界：查 D3 不返回只在 D1 出现的关键词（每行属当前日期）。"""
    await db.run_sync(_seed)
    # K3 只在 D1 出现
    k3 = (await db.execute(
        text("INSERT INTO analysis.dim_keyword(site,keyword) VALUES (:s,'kw-d1only') RETURNING keyword_id"),
        {"s": SITE},
    )).scalar()
    await db.execute(
        text("INSERT INTO analysis.fact_daily(site,report_date,keyword_id,current_ranking) VALUES (:s,:d,:k,7)"),
        {"s": SITE, "d": D1, "k": k3},
    )
    await db.flush()
    resp = await service.search(db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3, perPage=100))
    assert k3 not in {r["keyword_id"] for r in resp.data}


async def test_filtered_total_reflects_filter(db):
    """带筛选时总数按上限策略在作用域内计算，不用无筛选的 site_data_stats 值。"""
    await db.run_sync(_seed)  # D3 有 K1(kw-one)、K2(kw-two)，site_data_stats=2
    resp = await service.search(
        db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3, keyword="one")
    )
    assert resp.total == 1  # 只匹配 kw-one，不是无筛选的 2
    assert all("one" in r["keyword"] for r in resp.data)


async def test_response_has_protable_contract(db):
    """列表响应含 ProTable 契约字段 {success,data,total} + 作用域回显。"""
    await db.run_sync(_seed)
    resp = await service.search(db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3))
    dumped = resp.model_dump()
    assert {"success", "data", "total"} <= dumped.keys()
    assert dumped["success"] is True and isinstance(dumped["data"], list)
    assert dumped["site"] == SITE and dumped["date"] == D3


async def test_default_site_when_omitted(db):
    """未传站点用配置默认站点（不返回全站点混合）。"""
    await db.run_sync(_seed)
    resp = await service.search(db, AnalysisSearchRequest(view="DAY", date=D3))
    from config import settings
    assert resp.site == settings.default_site


def _seed_categories(db):
    """三个带类目的关键词落在 D3，另有一个 Electronics 行落在 D1 用于验作用域边界。

    事实行**写入时就带 top1_product_id**，不事后 UPDATE——事实表只追加是这套数据模型的
    前提，测试里用 UPDATE 会示范一个生产上不成立的写法。

    返回 {类目名: keyword_id}。
    """
    kids = {}
    for category in ("Electronics", "Pet Supplies", "Home"):
        pid = (db.execute(
            text("INSERT INTO analysis.dim_product(asin,title,brand,category) "
                 "VALUES (:a,'T','ACME',:c) RETURNING product_id"),
            {"a": f"B0{abs(hash(category)) % 10**8:08d}", "c": category},
        )).scalar()
        kid = (db.execute(
            text("INSERT INTO analysis.dim_keyword(site,keyword) VALUES (:s,:k) RETURNING keyword_id"),
            {"s": SITE, "k": f"kw-{category}"},
        )).scalar()
        db.execute(
            text("INSERT INTO analysis.fact_daily"
                 "(site,report_date,keyword_id,current_ranking,top1_product_id) "
                 "VALUES (:s,:d,:k,20,:p)"),
            {"s": SITE, "d": D3, "k": kid, "p": pid},
        )
        if category == "Electronics":
            # 同类目、同关键词，但落在 D1：查 D3 时不该出现
            db.execute(
                text("INSERT INTO analysis.fact_daily"
                     "(site,report_date,keyword_id,current_ranking,top1_product_id) "
                     "VALUES (:s,:d,:k,21,:p)"),
                {"s": SITE, "d": D1, "k": kid, "p": pid},
            )
        kids[category] = kid
    db.flush()
    return kids


async def test_multi_category_filter_matches_any_selected(db):
    """多选类目：命中所选集合中任意一个，不含未选类目。"""
    await db.run_sync(_seed)
    kids = await db.run_sync(_seed_categories)
    resp = await service.search(db, AnalysisSearchRequest(
        site=SITE, view="DAY", date=D3, perPage=100,
        categories=["Electronics", "Pet Supplies"],
    ))
    got = {r["keyword_id"] for r in resp.data}
    assert got == {kids["Electronics"], kids["Pet Supplies"]}
    assert kids["Home"] not in got
    assert {r["top1_category"] for r in resp.data} == {"Electronics", "Pet Supplies"}
    assert resp.total == 2  # 带筛选时总数按筛选后计算，不是 site_data_stats 的值


async def test_multi_category_filter_stays_in_scope(db):
    """多选类目不突破作用域：D1 上的同一关键词行不出现在 D3 的结果里。"""
    await db.run_sync(_seed)
    await db.run_sync(_seed_categories)
    resp = await service.search(db, AnalysisSearchRequest(
        site=SITE, view="DAY", date=D3, perPage=100, categories=["Electronics"],
    ))
    assert len(resp.data) == 1  # D1 那行同关键词同类目，被日期边界挡在外面
    assert resp.data[0]["current_ranking"] == 20  # D3 的值，不是 D1 的 21


async def test_empty_categories_behaves_as_no_filter(db):
    """空集合视同不筛：结果与完全不传类目一致。"""
    await db.run_sync(_seed)
    await db.run_sync(_seed_categories)
    base = await service.search(db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3, perPage=100))
    empty = await service.search(db, AnalysisSearchRequest(
        site=SITE, view="DAY", date=D3, perPage=100, categories=[],
    ))
    assert {r["keyword_id"] for r in empty.data} == {r["keyword_id"] for r in base.data}
    assert empty.total == base.total
