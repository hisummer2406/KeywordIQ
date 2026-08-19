"""词库（我的词库）集成测试。

默认跳过，只在设置 TEST_DATABASE_URL 时运行。测试库必须已迁移（alembic upgrade head）。
每个测试独占一个事务，结束即回滚——service 层内部会 commit，但会话是挂在外层事务上的
（SQLAlchemy 2.0 在这种情形下走 SAVEPOINT），故外层回滚仍能清干净。

    TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run pytest test/test_keyword_library_integration.py -v

这里钉的是三件不接真库就验不了的事：**行集合由条目决定**（掉榜与无数据的词照样占一行）、
**上限整批拒绝**、**私有性由查询条件保证**（不是界面藏起来）。
"""
import os
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.table.toolbox import library_service as service
from app.table.toolbox.library_schemas import (
    GroupCreateRequest,
    GroupRenameRequest,
    WatchRequest,
)
from config import settings

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)

SITE = "US"
OTHER_SITE = "DE"
D1, D2, D3 = date(2026, 7, 8), date(2026, 7, 9), date(2026, 7, 10)


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def _ensure_partitions_sync() -> bool:
    """建好本文件所需的分区并提交（asyncpg 建不了带参数的 DDL）。"""
    from sqlalchemy import create_engine

    from app.table.analysis.partitioning import ensure_fact_partitions

    engine = create_engine(TEST_URL)
    try:
        with engine.begin() as conn:
            if conn.execute(text("SELECT to_regclass('analysis.keyword_group')")).scalar() is None:
                return False
            for site in (SITE, OTHER_SITE):
                ensure_fact_partitions(conn, "fact_daily", site, D1)
                ensure_fact_partitions(conn, "fact_weekly", site, D1)
            ensure_fact_partitions(conn, "fact_dropped", SITE, D1)
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
    # create_savepoint：service 层内部会 commit/rollback，默认模式下那个 rollback 会一路
    # 掀掉外层事务里已经建好的种子数据，接下来的语句便撞在外键上——而报出来的错误
    # （「已有同名分组」）与真正的原因毫无关系。让 service 的事务落在 SAVEPOINT 上，
    # 它 rollback 的就只是自己那一段。
    session = AsyncSession(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await connection.close()
        await engine.dispose()


def _seed(db):
    """两个用户 + 三个 US 关键词 + 一个 DE 关键词。

    K1 三天都在榜（5→3→1）；K2 在 D3 掉榜（fact_dropped，最后在榜 D2 排 40）；
    K3 三天都没有数据（只在维度表里）——三种盯盘状态各一个。
    """
    def _user(name):
        return db.execute(
            text("INSERT INTO analysis.user_center(user_name,hashed_pwd,is_active,is_super) "
                 "VALUES (:n,'x',true,false) RETURNING id"),
            {"n": name},
        ).scalar()

    def _kw(site, kw):
        return db.execute(
            text("INSERT INTO analysis.dim_keyword(site,keyword) VALUES (:s,:k) RETURNING keyword_id"),
            {"s": site, "k": kw},
        ).scalar()

    owner = _user("lib-owner")
    other = _user("lib-other")
    k1, k2, k3 = _kw(SITE, "kw-listed"), _kw(SITE, "kw-dropped"), _kw(SITE, "kw-missing")
    k_de = _kw(OTHER_SITE, "kw-de")

    for d, rk in [(D1, 5), (D2, 3), (D3, 1)]:
        db.execute(
            text("INSERT INTO analysis.fact_daily(site,report_date,keyword_id,current_ranking,"
                 "top1_click_share) VALUES (:s,:d,:k,:r,1.5)"),
            {"s": SITE, "d": d, "k": k1, "r": rk},
        )
    for d, rk in [(D1, 50), (D2, 40)]:
        db.execute(
            text("INSERT INTO analysis.fact_daily(site,report_date,keyword_id,current_ranking) "
                 "VALUES (:s,:d,:k,:r)"),
            {"s": SITE, "d": d, "k": k2, "r": rk},
        )
    db.execute(
        text("INSERT INTO analysis.fact_dropped(site,period_type,anchor_date,keyword_id,"
             "last_seen_date,last_ranking) VALUES (:s,'DAY',:d,:k,:seen,40)"),
        {"s": SITE, "d": D3, "k": k2, "seen": D2},
    )
    for d, n in [(D1, 2), (D2, 2), (D3, 1)]:
        db.execute(
            text("INSERT INTO analysis.site_data_stats(site,period_type,report_date,row_count) "
                 "VALUES (:s,'DAY',:d,:n)"),
            {"s": SITE, "d": d, "n": n},
        )
    db.flush()
    return {"owner": owner, "other": other, "k1": k1, "k2": k2, "k3": k3, "k_de": k_de}


async def _group_with_items(db, seed, keyword_ids, name="主推词"):
    group = await service.create_group(db, seed["owner"], GroupCreateRequest(site=SITE, name=name))
    if keyword_ids:
        await service.add_items(db, seed["owner"], group.group_id, keyword_ids)
    return group


# ---------- 行集合由条目决定 ----------

async def test_三种状态各占一行(db):
    seed = await db.run_sync(_seed)
    group = await _group_with_items(db, seed, [seed["k1"], seed["k2"], seed["k3"]])

    resp = await service.watch(db, seed["owner"], WatchRequest(group_id=group.group_id, date=D3))

    assert resp.total == 3, "行集合是清单，不是命中事实表的行数"
    by_id = {r.keyword_id: r for r in resp.data}
    assert by_id[seed["k1"]].status == "LISTED"
    assert by_id[seed["k2"]].status == "DROPPED"
    assert by_id[seed["k3"]].status == "MISSING"


async def test_掉榜行带上期排名与最后在榜期次(db):
    seed = await db.run_sync(_seed)
    group = await _group_with_items(db, seed, [seed["k2"]])
    resp = await service.watch(db, seed["owner"], WatchRequest(group_id=group.group_id, date=D3))
    (row,) = resp.data
    assert (row.is_dropped, row.last_ranking, row.last_seen_date) == (True, 40, D2)


async def test_无数据行的指标留空而不是零排名(db):
    """留空与「排名 0」不是一回事：0 已经是"无上期"的编码。"""
    seed = await db.run_sync(_seed)
    group = await _group_with_items(db, seed, [seed["k3"]])
    resp = await service.watch(db, seed["owner"], WatchRequest(group_id=group.group_id, date=D3))
    (row,) = resp.data
    assert row.current_ranking is None and row.status == "MISSING"


async def test_降序时掉榜与无数据仍在末尾(db):
    """段序不随升降序翻转——否则「最差的词」和「没数据的词」看起来是一回事。"""
    seed = await db.run_sync(_seed)
    group = await _group_with_items(db, seed, [seed["k1"], seed["k2"], seed["k3"]])
    resp = await service.watch(
        db, seed["owner"],
        WatchRequest(group_id=group.group_id, date=D3, orderDir="desc"),
    )
    assert [r.status for r in resp.data] == ["LISTED", "DROPPED", "MISSING"]


async def test_走势点数按视图裁剪(db):
    """天视图 7 点：漏传 on/view 会画出 8 点，而那种错误没有任何地方会报错。"""
    seed = await db.run_sync(_seed)
    group = await _group_with_items(db, seed, [seed["k1"]])
    resp = await service.watch(db, seed["owner"], WatchRequest(group_id=group.group_id, date=D3))
    (row,) = resp.data
    assert row.trend == [5, 3, 1]
    assert row.trend_dates == [D1, D2, D3]
    assert len(row.trend) <= 7


async def test_空分组返回零行且不报错(db):
    seed = await db.run_sync(_seed)
    group = await _group_with_items(db, seed, [])
    resp = await service.watch(db, seed["owner"], WatchRequest(group_id=group.group_id, date=D3))
    assert (resp.total, resp.data) == (0, [])


async def test_满员分组能跑完一次盯盘查询(db):
    """上限同时是这条查询的规模保证：放宽上限前必须重测它。"""
    seed = await db.run_sync(_seed)
    cap = settings.LIBRARY_MAX_ITEMS_PER_GROUP

    def _bulk(sync_db):
        ids = []
        for i in range(cap):
            ids.append(sync_db.execute(
                text("INSERT INTO analysis.dim_keyword(site,keyword) VALUES (:s,:k) "
                     "RETURNING keyword_id"),
                {"s": SITE, "k": f"bulk-{i}"},
            ).scalar())
        sync_db.flush()
        return ids

    ids = await db.run_sync(_bulk)
    group = await service.create_group(db, seed["owner"], GroupCreateRequest(site=SITE, name="满员"))
    for start in range(0, cap, 500):
        await service.add_items(db, seed["owner"], group.group_id, ids[start:start + 500])

    resp = await service.watch(
        db, seed["owner"], WatchRequest(group_id=group.group_id, date=D3, perPage=200)
    )
    assert resp.total == cap and len(resp.data) == 200


# ---------- 条目写入 ----------

async def test_重复加词幂等且分别回传新增与已存在(db):
    seed = await db.run_sync(_seed)
    group = await _group_with_items(db, seed, [seed["k1"]])

    result = await service.add_items(db, seed["owner"], group.group_id, [seed["k1"], seed["k2"]])

    assert (result.added, result.existed, result.item_count) == (1, 1, 2)


async def test_同一请求里重复的id只算一条(db):
    seed = await db.run_sync(_seed)
    group = await _group_with_items(db, seed, [])
    result = await service.add_items(
        db, seed["owner"], group.group_id, [seed["k1"], seed["k1"], seed["k1"]]
    )
    assert (result.added, result.item_count) == (1, 1)


async def test_跨站点加词被拒且整批不写(db):
    seed = await db.run_sync(_seed)
    group = await _group_with_items(db, seed, [])

    with pytest.raises(service.KeywordSiteMismatchError):
        await service.add_items(db, seed["owner"], group.group_id, [seed["k1"], seed["k_de"]])

    resp = await service.watch(db, seed["owner"], WatchRequest(group_id=group.group_id, date=D3))
    assert resp.total == 0, "同批次里合法的那个词也不该被写入"


async def test_不存在的关键词被拒(db):
    seed = await db.run_sync(_seed)
    group = await _group_with_items(db, seed, [])
    with pytest.raises(service.KeywordSiteMismatchError):
        await service.add_items(db, seed["owner"], group.group_id, [999_999_999])


async def test_超出条目上限整批拒绝而不截断(db, monkeypatch):
    """截断后的清单与用户以为存进去的不是同一份，而他无从察觉。"""
    seed = await db.run_sync(_seed)
    monkeypatch.setattr(settings, "LIBRARY_MAX_ITEMS_PER_GROUP", 2)
    group = await _group_with_items(db, seed, [seed["k1"]])

    with pytest.raises(service.LibraryLimitError):
        await service.add_items(db, seed["owner"], group.group_id, [seed["k2"], seed["k3"]])

    resp = await service.watch(db, seed["owner"], WatchRequest(group_id=group.group_id, date=D3))
    assert resp.total == 1


async def test_超出分组数上限被拒(db, monkeypatch):
    seed = await db.run_sync(_seed)
    monkeypatch.setattr(settings, "LIBRARY_MAX_GROUPS_PER_SITE", 1)
    await service.create_group(db, seed["owner"], GroupCreateRequest(site=SITE, name="第一个"))
    with pytest.raises(service.LibraryLimitError):
        await service.create_group(db, seed["owner"], GroupCreateRequest(site=SITE, name="第二个"))


async def test_移除条目(db):
    seed = await db.run_sync(_seed)
    group = await _group_with_items(db, seed, [seed["k1"], seed["k2"]])
    result = await service.remove_items(db, seed["owner"], group.group_id, [seed["k1"]])
    assert (result.removed, result.item_count) == (1, 1)


# ---------- 归属与分组管理 ----------

async def test_别人的分组一律404(db):
    """不是 403：403 等于确认了这个 id 存在，而 id 是连续自增的。"""
    seed = await db.run_sync(_seed)
    group = await _group_with_items(db, seed, [seed["k1"]])

    with pytest.raises(service.GroupNotFoundError):
        await service.watch(db, seed["other"], WatchRequest(group_id=group.group_id, date=D3))
    with pytest.raises(service.GroupNotFoundError):
        await service.delete_group(db, seed["other"], group.group_id)


async def test_分组列表只列自己的(db):
    seed = await db.run_sync(_seed)
    await _group_with_items(db, seed, [], name="我的")
    await service.create_group(db, seed["other"], GroupCreateRequest(site=SITE, name="别人的"))

    mine = await service.list_groups(db, seed["owner"], SITE)
    assert [g.name for g in mine] == ["我的"]


async def test_同站点重名被拒跨站点同名可以(db):
    seed = await db.run_sync(_seed)
    await service.create_group(db, seed["owner"], GroupCreateRequest(site=SITE, name="重名"))
    with pytest.raises(service.GroupDuplicateError):
        await service.create_group(db, seed["owner"], GroupCreateRequest(site=SITE, name="重名"))
    de = await service.create_group(
        db, seed["owner"], GroupCreateRequest(site=OTHER_SITE, name="重名")
    )
    assert de.site == OTHER_SITE


async def test_分组列表带条目数(db):
    seed = await db.run_sync(_seed)
    await _group_with_items(db, seed, [seed["k1"], seed["k2"]])
    (group,) = await service.list_groups(db, seed["owner"], SITE)
    assert group.item_count == 2


async def test_改名不改站点(db):
    seed = await db.run_sync(_seed)
    group = await _group_with_items(db, seed, [seed["k1"]])
    renamed = await service.rename_group(
        db, seed["owner"], group.group_id, GroupRenameRequest(name="改过的名字")
    )
    assert (renamed.name, renamed.site, renamed.item_count) == ("改过的名字", SITE, 1)


async def test_删组连带删条目(db):
    seed = await db.run_sync(_seed)
    group = await _group_with_items(db, seed, [seed["k1"], seed["k2"]])

    await service.delete_group(db, seed["owner"], group.group_id)

    left = await db.execute(
        text("SELECT count(*) FROM analysis.keyword_group_item WHERE group_id = :g"),
        {"g": group.group_id},
    )
    assert left.scalar() == 0


# ---------- 作用域 ----------

async def test_作用域的站点取自分组(db):
    """盯盘屏没有站点选择器：站点由分组决定，否则会出现「选了 DE 却在看 US 分组」。"""
    seed = await db.run_sync(_seed)
    group = await _group_with_items(db, seed, [seed["k1"]])
    scope = await service.get_scope(db, seed["owner"], group.group_id, "DAY")
    assert scope.site == SITE and scope.dates == [D3, D2, D1]


async def test_未传日期取最新一期(db):
    seed = await db.run_sync(_seed)
    group = await _group_with_items(db, seed, [seed["k1"]])
    resp = await service.watch(db, seed["owner"], WatchRequest(group_id=group.group_id))
    assert resp.date == D3


async def test_非法日期报错不回退(db):
    seed = await db.run_sync(_seed)
    group = await _group_with_items(db, seed, [seed["k1"]])
    with pytest.raises(service.LibraryScopeError):
        await service.watch(
            db, seed["owner"], WatchRequest(group_id=group.group_id, date=date(2020, 1, 1))
        )
