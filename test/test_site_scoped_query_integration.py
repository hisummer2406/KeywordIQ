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
    resp = await service.search(db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3))
    assert resp.success and resp.date == D3
    assert resp.total == 2  # 来自 site_data_stats
    rankings = [r["current_ranking"] for r in resp.data]
    assert rankings == [1, 9]  # 默认按 current_ranking 升序


async def test_day_view_trend_without_week_change(db):
    k1, k2 = await db.run_sync(_seed)
    resp = await service.search(db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3))
    rows = {r["keyword_id"]: r for r in resp.data}
    assert rows[k1]["trend"] == [5, 3, 1]      # 按日期回溯
    assert rows[k2]["trend"] == [9]            # 只有 D3
    assert all("week_change" not in row for row in rows.values())


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


async def test_week_view_trend_reaches_across_report_periods(db):
    await db.run_sync(_seed)
    resp = await service.search(db, AnalysisSearchRequest(site=SITE, view="WEEK", date=WEEK))
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
    resp = await service.search(db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3, perPage=100))
    assert k3 not in {r["keyword_id"] for r in resp.data}


async def test_filtered_total_reflects_filter(db):
    """带筛选时总数在作用域内实算，不用无筛选的 site_data_stats 值。"""
    await db.run_sync(_seed)  # D3 有 K1(kw-one)、K2(kw-two)，site_data_stats=2
    resp = await service.search(
        db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3, keyword="one")
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
        db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3, ranking_min=0, perPage=1)
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
    resp = await service.search(db, req)

    assert resp.total is None
    assert len(resp.data) == 2  # 行照常返回，省掉的只是计数


async def test_include_total_defaults_to_true(db):
    """缺省要算总数——漏传的后果应当是"多算一次"，而不是"没有总数"。"""
    await db.run_sync(_seed)
    resp = await service.search(
        db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3, ranking_min=0)
    )
    assert resp.total == 2


async def test_unfiltered_total_ignores_include_total(db):
    """无筛选时总数读 site_data_stats（O(1)），便宜到不值得跳过，故不受开关影响。

    少一条分支，也少一处"这次到底算没算"的歧义。
    """
    await db.run_sync(_seed)
    resp = await service.search(
        db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3, include_total=False)
    )
    assert resp.total == 2


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


async def _row_for(db, keyword_id: int, **kwargs):
    resp = await service.search(db, AnalysisSearchRequest(
        site=SITE, view="DAY", date=D3, perPage=100, **kwargs,
    ))
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


async def test_share_filter_only_considers_first_slot(db):
    """份额区间只看第一名：第二名 30% 落在 20~40 区间内，但该行仍不该被选中。"""
    await db.run_sync(_seed)
    kids = await db.run_sync(_seed_top_slots)

    miss, _ = await _row_for(db, kids["kw-full"], click_share_min=20, click_share_max=40)
    assert miss is None  # 第一名是 50%，不在区间内

    hit, _ = await _row_for(db, kids["kw-full"], click_share_min=45, click_share_max=55)
    assert hit is not None


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

    resp = await service.search(db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3))

    assert resp.pending_rebuild is True


async def test_rebuilt_slice_is_not_flagged(db):
    """有品牌数据的切片不该被标记——否则提示会一直挂着，很快就没人看了。"""
    await db.run_sync(_seed)
    await db.run_sync(_seed_top_slots)  # 这个夹具写了 top1..3_brand_id

    resp = await service.search(db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3))

    assert resp.pending_rebuild is False
