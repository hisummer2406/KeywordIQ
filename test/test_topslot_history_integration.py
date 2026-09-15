"""ASIN/品牌悬浮历史（add-topslot-history-popover）集成测试。

默认跳过，只在设置 TEST_DATABASE_URL 时运行（沿用 V2 集成测试的 gate 模式，见
test_keyword_trend_integration.py）。覆盖 issue #96 的核心口径：单关键词单站点范围、
同一期最多计一次且重复占槽取最佳名次、跨关键词/跨站点/跨视图不串、历史范围到保留下界、
分页与精确计数、身份解析失败返回空结果而非报错。

    TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run pytest test/test_topslot_history_integration.py -v
"""
import os
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.table.analysis import topslot_history_service as service
from app.table.analysis.partitioning import retention_cutoff
from config import settings

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)

SITE = "US"
OTHER_SITE = "DE"
TODAY = date.today()
CUTOFF = retention_cutoff(TODAY, settings.HOT_WINDOW_MONTHS)

D0, D1, D2, D3 = (TODAY - timedelta(days=i) for i in (0, 2, 4, 6))
STALE_BEFORE_CUTOFF = CUTOFF - timedelta(days=10)

ASIN = "B0TESTASIN1"
BRAND = "Acme Corp"


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def _ensure_partitions_sync() -> bool:
    from sqlalchemy import create_engine

    from app.table.analysis.partitioning import ensure_fact_partitions

    engine = create_engine(TEST_URL)
    try:
        with engine.begin() as conn:
            if conn.execute(text("SELECT to_regclass('analysis.fact_daily')")).scalar() is None:
                return False
            for d in (D0, D1, D2, D3, STALE_BEFORE_CUTOFF):
                ensure_fact_partitions(conn, "fact_daily", SITE, d)
                ensure_fact_partitions(conn, "fact_daily", OTHER_SITE, d)
            ensure_fact_partitions(conn, "fact_weekly", SITE, D0)
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


def _insert_keyword(db, site, keyword):
    return db.execute(
        text("INSERT INTO analysis.dim_keyword(site,keyword) VALUES (:s,:k) RETURNING keyword_id"),
        {"s": site, "k": keyword},
    ).scalar()


def _insert_product(db, asin):
    return db.execute(
        text(
            "INSERT INTO analysis.dim_product(asin,title) VALUES (:a,'t') "
            "ON CONFLICT (asin) DO UPDATE SET asin=EXCLUDED.asin RETURNING product_id"
        ),
        {"a": asin},
    ).scalar()


def _insert_brand(db, name):
    return db.execute(
        text(
            "INSERT INTO analysis.dim_brand(name) VALUES (:n) "
            "ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING brand_id"
        ),
        {"n": name},
    ).scalar()


def _insert_fact_daily(db, *, site, report_date, keyword_id, **slots):
    cols = ["site", "report_date", "keyword_id", "current_ranking"] + list(slots.keys())
    params = {"site": site, "report_date": report_date, "keyword_id": keyword_id, "current_ranking": 1}
    params.update(slots)
    placeholders = ",".join(f":{c}" for c in cols)
    db.execute(
        text(f"INSERT INTO analysis.fact_daily({','.join(cols)}) VALUES ({placeholders})"),
        params,
    )


def _insert_fact_weekly(db, *, site, report_date_week, keyword_id, **slots):
    cols = ["site", "report_date_week", "keyword_id", "current_ranking"] + list(slots.keys())
    params = {"site": site, "report_date_week": report_date_week, "keyword_id": keyword_id,
              "current_ranking": 1}
    params.update(slots)
    placeholders = ",".join(f":{c}" for c in cols)
    db.execute(
        text(f"INSERT INTO analysis.fact_weekly({','.join(cols)}) VALUES ({placeholders})"),
        params,
    )


def _seed(db):
    """关键词 K1（US）：目标 ASIN 在 D0(top1)/D1(top3)/D2(top2) 三期出现，D3 处于同期
    top1 与 top2 双占（重复占槽，须只计一期、取最佳名次 1）；STALE 期同样放该 ASIN 在
    top1，用来断言保留窗口外的期次不计入。

    另建 K2（US，另一个关键词）与 K1_DE（DE，同名关键词）各自也让该 ASIN 上榜，
    用来断言不跨关键词、不跨站点汇总。

    品牌 BRAND 在 K1 的 D0(top1_brand)/D2(top3_brand) 两期出现。
    """
    # 本函数经顶层 `await db.run_sync(_seed)` 调用，此处的 `db` 已经是同步 Session，
    # 不要再嵌套 `run_sync`——那是把同步 Session 当异步用，会直接 AttributeError。
    pid = _insert_product(db, ASIN)
    bid = _insert_brand(db, BRAND)
    other_pid = _insert_product(db, "B0OTHERASIN")

    k1 = _insert_keyword(db, SITE, "topslot-history kw1")
    k2 = _insert_keyword(db, SITE, "topslot-history kw2")
    k1_de = _insert_keyword(db, OTHER_SITE, "topslot-history kw1")

    _insert_fact_daily(
        db, site=SITE, report_date=D0, keyword_id=k1,
        top1_product_id=pid, top1_brand_id=bid,
    )
    _insert_fact_daily(
        db, site=SITE, report_date=D1, keyword_id=k1,
        top3_product_id=pid, top2_brand_id=other_pid,  # 干扰值，非目标品牌
    )
    _insert_fact_daily(
        db, site=SITE, report_date=D2, keyword_id=k1,
        top2_product_id=pid, top3_brand_id=bid,
    )
    _insert_fact_daily(
        db, site=SITE, report_date=D3, keyword_id=k1,
        top1_product_id=pid, top2_product_id=pid,  # 同期重复占槽（防御场景）
    )
    _insert_fact_daily(
        db, site=SITE, report_date=STALE_BEFORE_CUTOFF, keyword_id=k1,
        top1_product_id=pid,
    )
    # 其他关键词/站点同样上榜该 ASIN，不该被计入 K1(US) 的历史
    _insert_fact_daily(db, site=SITE, report_date=D0, keyword_id=k2, top1_product_id=pid)
    _insert_fact_daily(
        db, site=OTHER_SITE, report_date=D0, keyword_id=k1_de, top1_product_id=pid,
    )
    # 周报也放一条同 ASIN，验证日视图查询不会混入
    _insert_fact_weekly(db, site=SITE, report_date_week=D0, keyword_id=k1, top1_product_id=pid)
    return k1


async def _history(db, k1, **overrides):
    params = dict(site=SITE, view="DAY", keyword_id=k1, kind="ASIN", value=ASIN, on=D0,
                  page=1, page_size=20)
    params.update(overrides)
    return await service.get_topslot_history(db, **params)


async def test_counts_only_periods_where_asin_actually_ranked(db):
    k1 = await db.run_sync(_seed)
    resp = await _history(db, k1)
    # D0/D1/D2/D3 四期命中（STALE 在保留窗口外，不计入）
    assert resp.total == 4


async def test_rank_reflects_actual_slot_per_period_desc_by_date(db):
    k1 = await db.run_sync(_seed)
    resp = await _history(db, k1)
    by_date = {item.date: item.rank for item in resp.items}
    assert by_date[D0] == 1
    assert by_date[D1] == 3
    assert by_date[D2] == 2
    # 按日期倒序
    assert [item.date for item in resp.items] == sorted(by_date, reverse=True)


async def test_duplicate_slot_in_same_period_counts_once_with_best_rank(db):
    k1 = await db.run_sync(_seed)
    resp = await _history(db, k1)
    d3_items = [item for item in resp.items if item.date == D3]
    assert len(d3_items) == 1
    assert d3_items[0].rank == 1


async def test_other_keyword_not_counted(db):
    k1 = await db.run_sync(_seed)
    resp = await _history(db, k1)
    assert resp.total == 4  # 不含 k2 那一期


async def test_other_site_not_counted(db):
    k1 = await db.run_sync(_seed)
    resp = await _history(db, k1)
    assert resp.total == 4  # 不含 DE 站点那一期


async def test_out_of_retention_period_excluded(db):
    k1 = await db.run_sync(_seed)
    resp = await _history(db, k1)
    assert all(item.date != STALE_BEFORE_CUTOFF for item in resp.items)


async def test_weekly_view_does_not_include_daily_periods(db):
    k1 = await db.run_sync(_seed)
    resp = await _history(db, k1, view="WEEK")
    assert resp.total == 1
    assert resp.items[0].date == D0


async def test_brand_history_reads_from_brand_slots_only(db):
    k1 = await db.run_sync(_seed)
    resp = await _history(db, k1, kind="BRAND", value=BRAND)
    assert resp.total == 2
    by_date = {item.date: item.rank for item in resp.items}
    assert by_date[D0] == 1
    assert by_date[D2] == 3


async def test_pagination_total_independent_of_page_size(db):
    k1 = await db.run_sync(_seed)
    resp = await _history(db, k1, page=1, page_size=2)
    assert resp.total == 4
    assert len(resp.items) == 2

    resp_p2 = await _history(db, k1, page=2, page_size=2)
    assert resp_p2.total == 4
    assert len(resp_p2.items) == 2

    resp_p3 = await _history(db, k1, page=3, page_size=2)
    assert resp_p3.total == 4
    assert resp_p3.items == []


async def test_unknown_asin_returns_empty_result_not_error(db):
    k1 = await db.run_sync(_seed)
    resp = await _history(db, k1, value="B0NEVERUSED9")
    assert resp.total == 0
    assert resp.items == []


async def test_cross_site_keyword_id_rejected(db):
    k1 = await db.run_sync(_seed)
    with pytest.raises(service.TopslotSiteMismatchError):
        await _history(db, k1, site=OTHER_SITE)


async def test_unknown_keyword_id_returns_not_found(db):
    await db.run_sync(_seed)
    with pytest.raises(service.TopslotKeywordNotFoundError):
        await _history(db, 999_999_999)
