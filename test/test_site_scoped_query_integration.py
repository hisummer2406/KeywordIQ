"""站点作用域查询（site-scoped-query）集成测试。

默认跳过，只在设置 TEST_DATABASE_URL 时运行（沿用 V2 集成测试的 gate 模式）。测试库必须
已迁移（alembic upgrade head）。每个测试独占一个事务，结束即回滚，零残留。

用受控小样本验证 A 期（作用域/筛选/排序/总数）与 B 期（7 天走势）——合成数据
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
W1, W2, WEEK = date(2026, 6, 26), date(2026, 7, 3), date(2026, 7, 10)

# 大多数用例不关心「谁在查」，固定一个占位 user_id——真实身份隔离由
# 「竞对新品」那组测试用真实 user_center 行覆盖（issue #56 P2）。
USER_ID = 1


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
            ensure_fact_partitions(conn, "fact_weekly", SITE, W1)
            ensure_fact_partitions(conn, "fact_weekly", SITE, WEEK)
            # 掉榜表的分区同样要建。漏掉它的表现正是本文件注释里那种最难归因的形态：
            # 全新库上第一次跑红、重跑就绿——因为第一次跑虽然失败了，却把分区 DDL 提交
            # 了下来。合并后在 keywordiq_test 上真实发生过一次（3 个导出用例首跑失败）。
            ensure_fact_partitions(conn, "fact_dropped", SITE, D1)
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
    for week_date, ranking in ((W1, 9), (W2, 5), (WEEK, 1)):
        db.execute(
            text("INSERT INTO analysis.fact_weekly(site,report_date_week,keyword_id,current_ranking,ranking_change) "
                 "VALUES (:s,:w,:k,:r,4)"),
            {"s": SITE, "w": week_date, "k": k1, "r": ranking},
        )
    # site_data_stats：日期选择器与总数来源
    for d, n in [(D1, 1), (D2, 1), (D3, 2)]:
        db.execute(
            text("INSERT INTO analysis.site_data_stats(site,period_type,report_date,row_count) "
                 "VALUES (:s,'DAY',:d,:n)"),
            {"s": SITE, "d": d, "n": n},
        )
    for week_date in (W1, W2, WEEK):
        db.execute(
            text("INSERT INTO analysis.site_data_stats(site,period_type,report_date,row_count) "
                 "VALUES (:s,'WEEK',:w,1)"),
            {"s": SITE, "w": week_date},
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
    resp = await service.search(db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3), user_id=USER_ID)
    assert resp.success and resp.date == D3
    assert resp.total == 2  # 来自 site_data_stats
    rankings = [r["current_ranking"] for r in resp.data]
    assert rankings == [1, 9]  # 默认按 current_ranking 升序


async def test_day_view_trend_without_week_change(db):
    k1, k2 = await db.run_sync(_seed)
    resp = await service.search(db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3), user_id=USER_ID)
    rows = {r["keyword_id"]: r for r in resp.data}
    assert rows[k1]["trend"] == [5, 3, 1]      # 按日期回溯
    assert rows[k2]["trend"] == [9]            # 只有 D3
    assert all("week_change" not in row for row in rows.values())


async def test_trend_is_as_of_selected_date(db):
    k1, _ = await db.run_sync(_seed)
    resp = await service.search(db, AnalysisSearchRequest(site=SITE, view="DAY", date=D2), user_id=USER_ID)
    rows = {r["keyword_id"]: r for r in resp.data}
    assert rows[k1]["trend"] == [5, 3]  # 截至 D2，不含 D3


async def test_sort_whitelist_rejects_trend(db):
    await db.run_sync(_seed)
    # 非法排序字段 trend → 回落默认 current_ranking 升序
    resp = await service.search(
        db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3, orderBy="trend"),
        user_id=USER_ID,
    )
    assert [r["current_ranking"] for r in resp.data] == [1, 9]


async def test_invalid_date_raises_scope_error(db):
    await db.run_sync(_seed)
    with pytest.raises(service.ScopeError):
        await service.search(db, AnalysisSearchRequest(site=SITE, view="DAY", date=date(2020, 1, 1)), user_id=USER_ID)


async def test_week_view_trend_reaches_across_report_periods(db):
    await db.run_sync(_seed)
    resp = await service.search(db, AnalysisSearchRequest(site=SITE, view="WEEK", date=WEEK), user_id=USER_ID)
    assert resp.total == 1
    assert resp.data[0]["trend"] == [9, 5, 1]
    assert resp.data[0]["trend_dates"] == [W1, W2, WEEK]
    assert "week_change" not in resp.data[0]


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
    resp = await service.search(db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3, perPage=100), user_id=USER_ID)
    assert k3 not in {r["keyword_id"] for r in resp.data}


async def test_filtered_total_reflects_filter(db):
    """带筛选时总数在作用域内实算，不用无筛选的 site_data_stats 值。"""
    await db.run_sync(_seed)  # D3 有 K1(kw-one)、K2(kw-two)，site_data_stats=2
    resp = await service.search(
        db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3, keyword="one"),
        user_id=USER_ID,
    )
    assert resp.total == 1  # 只匹配 kw-one，不是无筛选的 2
    assert all("one" in r["keyword"] for r in resp.data)


async def test_filtered_total_is_exact_beyond_page_size(db):
    """带筛选的总数**必须精确**，且与分页大小无关。

    这里曾有一个 `FILTERED_COUNT_CAP = 10000`：命中超过一万时 total 就固定返回 10000，
    而调用方无法把它与"恰好一万行"区分开。前端据此算翻页页数，于是超出的命中行在界面上
    根本够不着——数据在、接口也能返回，就是没有那个页码按钮。

    用 perPage=1 取一页：total 必须是命中总数 2，不能是"这一页有几行"，也不能被任何
    上限截断。断言写成与 perPage 无关的形式，正是因为封顶那版在小数据上看起来是对的。
    """
    await db.run_sync(_seed)
    resp = await service.search(
        db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3, ranking_min=0, perPage=1),
        user_id=USER_ID,
    )
    assert len(resp.data) == 1
    assert resp.total == 2


async def test_include_total_false_skips_the_count(db):
    """``include_total=False`` 时不算总数，返回 ``None`` 而不是 0。

    总数是精确计数，代价与命中行数同阶（周报最坏约 0.9s），而同一组条件下翻页、改排序、
    改每页条数都不会改变它——每页重算一遍浪费的正好是这条路径上最贵的部分。

    **必须是 None 不是 0**：0 的含义是"确实没有命中行"，前端据此会把翻页器清空。
    两者混淆的表现是"翻到第二页表格突然说没有数据了"，且看不出原因。
    """
    await db.run_sync(_seed)
    req = AnalysisSearchRequest(
        site=SITE, view="DAY", date=D3, ranking_min=0, include_total=False
    )
    resp = await service.search(db, req, user_id=USER_ID)

    assert resp.total is None
    assert len(resp.data) == 2  # 行照常返回，省掉的只是计数


async def test_include_total_defaults_to_true(db):
    """缺省要算总数——漏传的后果应当是"多算一次"，而不是"没有总数"。"""
    await db.run_sync(_seed)
    resp = await service.search(
        db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3, ranking_min=0),
        user_id=USER_ID,
    )
    assert resp.total == 2


async def test_unfiltered_total_ignores_include_total(db):
    """无筛选时总数读 site_data_stats（O(1)），便宜到不值得跳过，故不受开关影响。

    少一条分支，也少一处"这次到底算没算"的歧义。
    """
    await db.run_sync(_seed)
    resp = await service.search(
        db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3, include_total=False),
        user_id=USER_ID,
    )
    assert resp.total == 2


async def test_response_has_protable_contract(db):
    """列表响应含 ProTable 契约字段 {success,data,total} + 作用域回显。"""
    await db.run_sync(_seed)
    resp = await service.search(db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3), user_id=USER_ID)
    dumped = resp.model_dump()
    assert {"success", "data", "total"} <= dumped.keys()
    assert dumped["success"] is True and isinstance(dumped["data"], list)
    assert dumped["site"] == SITE and dumped["date"] == D3


async def test_default_site_when_omitted(db):
    """未传站点用配置默认站点（不返回全站点混合）。"""
    await db.run_sync(_seed)
    resp = await service.search(db, AnalysisSearchRequest(view="DAY", date=D3), user_id=USER_ID)
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
            text("INSERT INTO analysis.dim_product(asin,title) "
                 "VALUES (:a,'T') RETURNING product_id"),
            {"a": f"B0{abs(hash(category)) % 10**8:08d}"},
        )).scalar()
        cid = (db.execute(
            text("INSERT INTO analysis.dim_category(name) VALUES (:c) "
                 "ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING category_id"),
            {"c": category},
        )).scalar()
        kid = (db.execute(
            text("INSERT INTO analysis.dim_keyword(site,keyword) VALUES (:s,:k) RETURNING keyword_id"),
            {"s": SITE, "k": f"kw-{category}"},
        )).scalar()
        db.execute(
            text("INSERT INTO analysis.fact_daily"
                 "(site,report_date,keyword_id,current_ranking,top1_product_id,top1_category_id) "
                 "VALUES (:s,:d,:k,20,:p,:c)"),
            {"s": SITE, "d": D3, "k": kid, "p": pid, "c": cid},
        )
        if category == "Electronics":
            # 同类目、同关键词，但落在 D1：查 D3 时不该出现
            db.execute(
                text("INSERT INTO analysis.fact_daily"
                     "(site,report_date,keyword_id,current_ranking,top1_product_id,top1_category_id) "
                     "VALUES (:s,:d,:k,21,:p,:c)"),
                {"s": SITE, "d": D1, "k": kid, "p": pid, "c": cid},
            )
        kids[category] = kid
    db.flush()
    return kids


async def test_multi_category_filter_matches_any_selected(db):
    """多选类目：命中所选集合中任意一个，不含未选类目。"""
    await db.run_sync(_seed)
    kids = await db.run_sync(_seed_categories)
    resp = await service.search(
        db, AnalysisSearchRequest(
            site=SITE, view="DAY", date=D3, perPage=100,
            categories=["Electronics", "Pet Supplies"],
        ),
        user_id=USER_ID,
    )
    got = {r["keyword_id"] for r in resp.data}
    assert got == {kids["Electronics"], kids["Pet Supplies"]}
    assert kids["Home"] not in got
    assert {r["top1_category"] for r in resp.data} == {"Electronics", "Pet Supplies"}
    assert resp.total == 2  # 带筛选时总数按筛选后计算，不是 site_data_stats 的值


async def test_multi_category_filter_stays_in_scope(db):
    """多选类目不突破作用域：D1 上的同一关键词行不出现在 D3 的结果里。"""
    await db.run_sync(_seed)
    await db.run_sync(_seed_categories)
    resp = await service.search(
        db, AnalysisSearchRequest(
            site=SITE, view="DAY", date=D3, perPage=100, categories=["Electronics"],
        ),
        user_id=USER_ID,
    )
    assert len(resp.data) == 1  # D1 那行同关键词同类目，被日期边界挡在外面
    assert resp.data[0]["current_ranking"] == 20  # D3 的值，不是 D1 的 21


async def test_empty_categories_behaves_as_no_filter(db):
    """空集合视同不筛：结果与完全不传类目一致。"""
    await db.run_sync(_seed)
    await db.run_sync(_seed_categories)
    base = await service.search(
        db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3, perPage=100), user_id=USER_ID,
    )
    empty = await service.search(
        db, AnalysisSearchRequest(
            site=SITE, view="DAY", date=D3, perPage=100, categories=[],
        ),
        user_id=USER_ID,
    )
    assert {r["keyword_id"] for r in empty.data} == {r["keyword_id"] for r in base.data}
    assert empty.total == base.total


# ---------------- 前三商品槽位（show-top3-products） ----------------

def _seed_top_slots(db):
    """两个关键词落在 D3：kw-full 三个槽位齐全，kw-partial 只有前两个。

    槽位与名次的对应关系是这批字段的全部意义，因此测试必须能区分"第三名为空"与
    "第三名被第二名顶替"。三个商品的品牌各不相同，好让筛选口径的断言有区分度。

    返回 {关键词: keyword_id}。
    """
    pids = []
    for slot in (1, 2, 3):
        pids.append((db.execute(
            text("INSERT INTO analysis.dim_product(asin,title) "
                 "VALUES (:a,:t) RETURNING product_id"),
            {"a": f"B0SLOT{slot:05d}", "t": f"Title {slot}"},
        )).scalar())
    # 品牌是**关键词级**排行，与商品槽位无关：BRAND2 落在第 2 名次上，
    # 用于证明「前三任一命中」真的生效——只测第 1 名次的话与旧行为无法区分。
    bids = [
        (db.execute(
            text("INSERT INTO analysis.dim_brand(name) VALUES (:b) "
                 "ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING brand_id"),
            {"b": f"BRAND{slot}"},
        )).scalar()
        for slot in (1, 2, 3)
    ]

    kids = {}
    for keyword, slots in (("kw-full", 3), ("kw-partial", 2)):
        kid = (db.execute(
            text("INSERT INTO analysis.dim_keyword(site,keyword) VALUES (:s,:k) RETURNING keyword_id"),
            {"s": SITE, "k": keyword},
        )).scalar()
        params = {"s": SITE, "d": D3, "k": kid, "p1": pids[0], "p2": pids[1],
                  "p3": pids[2] if slots == 3 else None,
                  "b1": bids[0], "b2": bids[1], "b3": bids[2]}
        db.execute(
            text("INSERT INTO analysis.fact_daily"
                 "(site,report_date,keyword_id,current_ranking,"
                 " top1_product_id,top1_click_share,top1_conversion_share,"
                 " top2_product_id,top2_click_share,top2_conversion_share,"
                 " top3_product_id,top3_click_share,top3_conversion_share,"
                 " top1_brand_id,top2_brand_id,top3_brand_id) "
                 "VALUES (:s,:d,:k,30,:p1,50.00,20.00,:p2,30.00,10.00,:p3,10.00,5.00,"
                 " :b1,:b2,:b3)"),
            params,
        )
        kids[keyword] = kid
    db.flush()
    return kids


async def _row_for(db, keyword_id: int, *, user_id: int = USER_ID, **kwargs):
    resp = await service.search(
        db, AnalysisSearchRequest(
            site=SITE, view="DAY", date=D3, perPage=100, **kwargs,
        ),
        user_id=user_id,
    )
    return next((r for r in resp.data if r["keyword_id"] == keyword_id), None), resp


async def test_row_carries_three_product_slots(db):
    """三个槽位齐全时，每组字段各自对应第一、第二、第三名。"""
    await db.run_sync(_seed)
    kids = await db.run_sync(_seed_top_slots)
    row, _ = await _row_for(db, kids["kw-full"])

    assert (row["top1_asin"], row["top1_brand"]) == ("B0SLOT00001", "BRAND1")
    assert (row["top2_asin"], row["top2_brand"]) == ("B0SLOT00002", "BRAND2")
    assert (row["top3_asin"], row["top3_brand"]) == ("B0SLOT00003", "BRAND3")
    assert [row["top1_click_share"], row["top2_click_share"], row["top3_click_share"]] == [50, 30, 10]
    assert [row["top1_conversion_share"], row["top2_conversion_share"],
            row["top3_conversion_share"]] == [20, 10, 5]


async def test_missing_slot_is_null_not_shifted(db):
    """只有两个商品：第三组为空，前两组仍是第一、第二名——不顺延。"""
    await db.run_sync(_seed)
    kids = await db.run_sync(_seed_top_slots)
    row, _ = await _row_for(db, kids["kw-partial"])

    assert row["top1_asin"] == "B0SLOT00001"
    assert row["top2_asin"] == "B0SLOT00002"
    assert row["top3_asin"] is None
    # 品牌名次**不随商品槽位缺失而缺失**——两者是彼此独立的排行。此前这里断言
    # top3_brand is None，把"没有第三个商品"和"没有第三个品牌"当成了同一件事，
    # 而那正是"品牌是商品属性"这个错误模型的残留。
    assert row["top3_brand"] == "BRAND3"


async def test_brand_filter_matches_any_of_top_three(db):
    """**前三任一命中**：第 2、第 3 名次的品牌同样能筛到。

    这是本变更与旧行为唯一能区分开的断言。旧口径是"只匹配点击量第一名"，只测第 1 名次
    的话两套实现都会通过——必须测非第一名次才有意义。

    展示口径与筛选口径由此一致：界面展示前三，筛选也匹配前三。
    """
    await db.run_sync(_seed)
    kids = await db.run_sync(_seed_top_slots)

    for brand in ("BRAND1", "BRAND2", "BRAND3"):
        hit, resp = await _row_for(db, kids["kw-full"], brands=[brand])
        assert hit is not None, f"{brand} 在前三名次里，应当命中"
        assert resp.total >= 1

    miss, resp = await _row_for(db, kids["kw-full"], brands=["NOT-IN-TOP3"])
    assert miss is None
    assert resp.total == 0


async def test_asin_filter_is_exact_not_substring(db):
    """ASIN 按等值匹配，片段不再命中。

    这是一次**有意的能力收窄**（issue #49）：ASIN 只能从顶部检索框输入，而那里只有整串
    匹配 ``^B0[A-Z0-9]{8}$`` 才会路由到这个字段——片段本来就到不了后端。换来的是
    ``dim_product`` 上 409 MB 的 asin GIN trgm 索引可以删掉；它由每次导入的每一行写入
    维护，而等值匹配走 ``uq_dim_product_asin`` 唯一索引就够。

    片段那条断言是本变更唯一能与旧行为区分开的：只测整串命中的话，``ILIKE '%...%'``
    也照样绿。
    """
    await db.run_sync(_seed)
    kids = await db.run_sync(_seed_top_slots)

    hit, resp = await _row_for(db, kids["kw-full"], asin="B0SLOT00001")
    assert hit is not None
    assert resp.total == 2  # kw-full 与 kw-partial 共用同一个第一名商品

    miss, resp = await _row_for(db, kids["kw-full"], asin="SLOT0000")
    assert miss is None
    assert resp.total == 0


async def test_asin_filter_accepts_lower_case_input(db):
    """小写输入照样命中——规范化在请求模型里做。

    检索框的判定正则带 ``/i``，小写整串同样被路由到 asin 字段，而等值匹配区分大小写。
    漏掉规范化的表现是「明明是正确的 ASIN 却查不到」，而界面上没有任何东西提示大小写
    要紧——旧的 ``ILIKE`` 把这件事隐含地办了。
    """
    await db.run_sync(_seed)
    kids = await db.run_sync(_seed_top_slots)

    hit, _ = await _row_for(db, kids["kw-full"], asin="b0slot00001")
    assert hit is not None


async def test_asin_filter_hits_second_and_third_slot(db):
    """**前三任一命中**：此前只匹配点击量第一名的槽位，一个实际排第二、第三名次的
    ASIN 粘进搜索框会查不到任何结果，而这个限制对用户不可见（issue #56 P2）。

    这条取代了原先钉住"只匹配 top1"的用例——那条断言的是本次改动明确要收窄掉的行为，
    继续留着会与新行为直接冲突。
    """
    await db.run_sync(_seed)
    kids = await db.run_sync(_seed_top_slots)

    for asin in ("B0SLOT00001", "B0SLOT00002", "B0SLOT00003"):
        hit, resp = await _row_for(db, kids["kw-full"], asin=asin)
        assert hit is not None, f"{asin} 在前三商品槽位里，应当命中"
        assert resp.total >= 1


async def test_share_filter_only_considers_first_slot(db):
    """份额区间只看第一名：第二名 30% 落在 20~40 区间内，但该行仍不该被选中。"""
    await db.run_sync(_seed)
    kids = await db.run_sync(_seed_top_slots)

    miss, _ = await _row_for(db, kids["kw-full"], click_share_min=20, click_share_max=40)
    assert miss is None  # 第一名是 50%，不在区间内

    hit, _ = await _row_for(db, kids["kw-full"], click_share_min=45, click_share_max=55)
    assert hit is not None


# --------------------------------------------------------------------------
# 竞对新品筛选（issue #56 P2）
#
# 命中口径：该词前三商品槽位任一 ASIN，属于当前用户在当前站点关注的卖家的在售集合
# （``competitor_seller_asin``，来自 issue #56 P3a 的插件被动采集）。判定按 user_id
# 私有、按 site 限定——与词库/竞对监控同一条纪律。依赖 ``_seed_top_slots`` 造好的
# B0SLOT00001/2/3 三个 ASIN。
# --------------------------------------------------------------------------


def _seed_competitor_watch(db):
    """四个用户覆盖四种关注形态：

    - ``owner``：在 US 关注一个卖家，其在售 ASIN 对应 kw-full 的**第二**名——覆盖
      非 top1 槽位；同时该卖家在 GB 站也有一条独立主体，被 owner 一并关注，用来在
      「跨站点」用例里验证这条不会泄漏进 US 的判定。
    - ``other``：在 US 关注另一个卖家，其在售 ASIN 对应**第三**名——与 owner 不重叠，
      验证身份隔离；kw-partial 没有第三个商品，故 other 的命中集合比 owner 窄，
      用于验证总数是精确计数而非预存值。
    - ``no_watch``：不关注任何卖家——最常见的初始状态，命中必须为空且不报错。
    - ``gb_only``：只在 GB 关注一个卖家，其 GB 在售 ASIN 恰与 kw-full 的**第一**名
      字符串相同——专门验证跨站点关注不该让 US 查询命中，即便 ASIN 字符串一致。

    返回 ``{"owner": id, "other": id, "no_watch": id, "gb_only": id}``。
    """
    def _user(name):
        return db.execute(
            text("INSERT INTO analysis.user_center(user_name,hashed_pwd,is_active,is_super) "
                 "VALUES (:n,'x',true,false) RETURNING id"),
            {"n": name},
        ).scalar()

    owner = _user("cn-owner")
    other = _user("cn-other")
    no_watch = _user("cn-no-watch")
    gb_only = _user("cn-gb-only")

    for seller_id, site in (
        ("A1OWNERSELLER", "US"),
        ("A1OTHERSELLER", "US"),
        ("A1OWNERSELLER", "GB"),   # 同一卖家在 GB 站是独立主体（seller_id, site 复合主键）
        ("A1GBONLYSELLER", "GB"),
    ):
        db.execute(
            text("INSERT INTO analysis.competitor_seller(seller_id,site) VALUES (:sid,:site)"),
            {"sid": seller_id, "site": site},
        )

    for seller_id, site, asin in (
        ("A1OWNERSELLER", "US", "B0SLOT00002"),   # 第二名，owner 应命中
        ("A1OTHERSELLER", "US", "B0SLOT00003"),   # 第三名，other 应命中且与 owner 不重叠
        ("A1OWNERSELLER", "GB", "B0SLOT00001"),   # 同卖家在 GB 的行，不该泄漏进 US 判定
        ("A1GBONLYSELLER", "GB", "B0SLOT00001"),  # 字符串与第一名相同，但 site 是 GB
    ):
        db.execute(
            text("INSERT INTO analysis.competitor_seller_asin(seller_id,site,asin) "
                 "VALUES (:sid,:site,:asin)"),
            {"sid": seller_id, "site": site, "asin": asin},
        )

    for user_id, seller_id, site in (
        (owner, "A1OWNERSELLER", "US"),
        (owner, "A1OWNERSELLER", "GB"),
        (other, "A1OTHERSELLER", "US"),
        (gb_only, "A1GBONLYSELLER", "GB"),
    ):
        db.execute(
            text("INSERT INTO analysis.user_seller_watch(user_id,seller_id,site) "
                 "VALUES (:u,:sid,:site)"),
            {"u": user_id, "sid": seller_id, "site": site},
        )
    db.flush()
    return {"owner": owner, "other": other, "no_watch": no_watch, "gb_only": gb_only}


async def test_competitor_new_filter_hits_second_slot_for_watching_user(db):
    """命中口径覆盖非 top1 槽位：owner 关注的卖家在售 ASIN 对应 kw-full 的第二名。"""
    await db.run_sync(_seed)
    kids = await db.run_sync(_seed_top_slots)
    watch = await db.run_sync(_seed_competitor_watch)

    hit, resp = await _row_for(
        db, kids["kw-full"], competitor_new=True, user_id=watch["owner"],
    )
    assert hit is not None
    assert resp.total >= 1


async def test_competitor_new_filter_isolates_by_user(db):
    """身份隔离：owner 与 other 各自关注不重叠的卖家，同一个词对两人都命中
    （各自的商品槽位不同），但对完全没有关注记录的第三个用户必须是 miss——
    命中不是"有没有人关注"这个全局判断，是"当前用户有没有关注"。
    """
    await db.run_sync(_seed)
    kids = await db.run_sync(_seed_top_slots)
    watch = await db.run_sync(_seed_competitor_watch)

    owner_hit, _ = await _row_for(
        db, kids["kw-full"], competitor_new=True, user_id=watch["owner"],
    )
    other_hit, _ = await _row_for(
        db, kids["kw-full"], competitor_new=True, user_id=watch["other"],
    )
    assert owner_hit is not None
    assert other_hit is not None

    no_watch_hit, resp = await _row_for(
        db, kids["kw-full"], competitor_new=True, user_id=watch["no_watch"],
    )
    assert no_watch_hit is None
    assert resp.total == 0


async def test_competitor_new_filter_scoped_to_site(db):
    """跨站点关注不影响判定：只在 GB 关注的卖家，其 GB 在售 ASIN 不该让 US 查询命中，
    即便 ASIN 字符串与 US 站某个词的第一名商品完全一致——子查询漏掉 site 限定的表现
    正是这种"看起来命中了，其实是另一个站点的行"。
    """
    await db.run_sync(_seed)
    kids = await db.run_sync(_seed_top_slots)
    watch = await db.run_sync(_seed_competitor_watch)

    miss, resp = await _row_for(
        db, kids["kw-full"], competitor_new=True, user_id=watch["gb_only"],
    )
    assert miss is None
    assert resp.total == 0


async def test_competitor_new_combines_with_brand_filter_as_and(db):
    """与其他筛选维度同一语义：勾选的所有条件同时成立，不是任一成立（design.md 决策 4）。

    kw-full 本身就带 BRAND2（第二名品牌），单独按 brands=["BRAND2"] 筛选必然命中——
    用 gb_only（US 站没有任何关注记录）同时勾 competitor_new 与这个必然满足的品牌条件，
    若两者是 OR，brand 那一半会把它救回来；必须是 AND 才会落空。owner 两个条件都满足，
    同样的品牌筛选应当命中，证明不是 competitor_new 单独把行挡掉了。
    """
    await db.run_sync(_seed)
    kids = await db.run_sync(_seed_top_slots)
    watch = await db.run_sync(_seed_competitor_watch)

    miss, _ = await _row_for(
        db, kids["kw-full"], competitor_new=True, brands=["BRAND2"], user_id=watch["gb_only"],
    )
    assert miss is None

    hit, _ = await _row_for(
        db, kids["kw-full"], competitor_new=True, brands=["BRAND2"], user_id=watch["owner"],
    )
    assert hit is not None


async def test_competitor_new_total_is_exact_hit_count(db):
    """只传 competitor_new 时总数是真实命中行数，不是 site_data_stats 的预存值
    （design.md 决策 3 的集成级验证：真库上比单测更有说服力）。

    other 关注的卖家只对应 kw-full 的第三名（kw-partial 没有第三个商品），真实命中
    只有 1 行；``_seed`` 预存的 D3 总数是 2——两者不相等，才能证明总数真的走了精确
    计数（``count_filtered``），而不是漏算 ``request_has_filters`` 后退回预存值、
    凑巧算对。
    """
    await db.run_sync(_seed)
    await db.run_sync(_seed_top_slots)
    watch = await db.run_sync(_seed_competitor_watch)

    resp = await service.search(
        db,
        AnalysisSearchRequest(site=SITE, view="DAY", date=D3, perPage=100, competitor_new=True),
        user_id=watch["other"],
    )
    assert resp.total == 1
    assert len(resp.data) == 1


# --------------------------------------------------------------------------
# 未重导切片的显式提示
#
# 品牌与类目从商品维度移出后，历史分区的六个新列全是 NULL，要靠按批次重导补齐。
# 重导完成前，按品牌筛选在这些日期上会返回空——**静默返回空是最难排查的形态**，
# 用户会以为是自己筛选条件写错了，而不是数据还没重建。
# --------------------------------------------------------------------------


async def test_response_flags_slice_pending_rebuild(db):
    """切片没有任何品牌数据时，响应要标出「待重建」。"""
    await db.run_sync(_seed)  # _seed 的行不带品牌/类目

    resp = await service.search(db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3), user_id=USER_ID)

    assert resp.pending_rebuild is True


async def test_rebuilt_slice_is_not_flagged(db):
    """有品牌数据的切片不该被标记——否则提示会一直挂着，很快就没人看了。"""
    await db.run_sync(_seed)
    await db.run_sync(_seed_top_slots)  # 这个夹具写了 top1..3_brand_id

    resp = await service.search(db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3), user_id=USER_ID)

    assert resp.pending_rebuild is False
