"""竞对 ASIN 上报的集成测试。

默认跳过，只在设置 TEST_DATABASE_URL 时运行。测试库必须已迁移到 head。

    TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run pytest test/test_competitor_asin_integration.py -v

这里钉的是**不接真库就验不了**的行为，逐条对应一个「错了不会报错」的失败形态：

- 未关注的卖家整个请求拒绝（共享层被任意写入）
- 幂等四态：同键同体回放 / 同键异体 409 / 跨用户同键互不影响 / 只有展示属性不同仍回放
- 重复上报不新增行、`first_seen_at` 不变（老品跳进"今天的新发现"）
- 基线只在首次落定，且首批 ASIN 不算新发现（首刷即产生数以万计的假条目）
- `captured_at` 偏移不影响任何判定（客户端时钟左右上新流）
- `first_report_id` 指向真正引入该行的那次上报（共享层被写脏后无法定位来源）
"""

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.common.errors import ConflictError, UnprocessableError
from app.table.competitor import asin_service as service
from app.table.competitor import store_service as service_store
from app.table.competitor import watch_service as service_watch
from app.table.competitor.asin_schemas import AsinReportItem, AsinReportRequest
from app.table.competitor.store_schemas import NewArrivalRequest

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)

_SELLER = "AS2G99DA1EUF"
_SITE = "US"


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def _tables_exist_sync() -> bool:
    from sqlalchemy import create_engine

    engine = create_engine(TEST_URL)
    try:
        with engine.begin() as conn:
            return (
                conn.execute(
                    text("SELECT to_regclass('analysis.competitor_seller_asin')")
                ).scalar()
                is not None
            )
    finally:
        engine.dispose()


@pytest.fixture()
async def db():
    if not _tables_exist_sync():
        pytest.skip("测试库未迁移，请先运行 alembic upgrade head")
    engine = create_async_engine(_async_url(TEST_URL), poolclass=NullPool)
    connection = await engine.connect()
    trans = await connection.begin()
    session = AsyncSession(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await connection.close()
        await engine.dispose()


def _seed(db):
    """两个用户、一个卖家；只有 owner 关注了它。"""

    def _user(name):
        return db.execute(
            text(
                "INSERT INTO analysis.user_center(user_name,hashed_pwd,is_active,is_super) "
                "VALUES (:n,'x',true,false) RETURNING id"
            ),
            {"n": name},
        ).scalar()

    owner = _user("asin-owner")
    other = _user("asin-other")
    db.execute(
        text(
            "INSERT INTO analysis.competitor_seller(seller_id,site) VALUES (:s,:t) "
            "ON CONFLICT DO NOTHING"
        ),
        {"s": _SELLER, "t": _SITE},
    )
    for uid in (owner, other):
        db.execute(
            text(
                "INSERT INTO analysis.user_seller_watch(user_id,seller_id,site,priority) "
                "VALUES (:u,:s,:t,1)"
            ),
            {"u": uid, "s": _SELLER, "t": _SITE},
        )
    db.flush()
    return {"owner": owner, "other": other}


def _req(request_id="rq-1", asins=("B0AAAAAAAA",), **kw) -> AsinReportRequest:
    base = {
        "request_id": request_id,
        "seller_id": _SELLER,
        "site": _SITE,
        "items": [AsinReportItem(asin=a) for a in asins],
    }
    base.update(kw)
    return AsinReportRequest(**base)


async def _count(db, sql: str, params: dict | None = None) -> int:
    return (await db.execute(text(sql), params or {})).scalar()


# ---------- 授权 ----------


async def test_未关注的卖家整个请求拒绝(db):
    """在售数据是共享层：不限制写入来源，任何持有令牌的用户都能污染其他人看到的数据。"""
    seed = await db.run_sync(_seed)
    await db.execute(
        text("DELETE FROM analysis.user_seller_watch WHERE user_id=:u"),
        {"u": seed["other"]},
    )
    with pytest.raises(UnprocessableError) as exc:
        await service.submit_report(db, seed["other"], _req())
    assert exc.value.code == "competitor.seller_not_watched"
    assert _SELLER in exc.value.message  # 用户要能看出是哪个卖家
    assert await _count(db, "SELECT count(*) FROM analysis.competitor_seller_asin") == 0


# ---------- 空批次与非法输入 ----------


async def test_空items整批拒绝不落定基线(db):
    """放行的话，首次上报会落一个不含任何 ASIN 的"空基线"——下一次正常上报时，该卖家的
    全部存量 ASIN 都晚于这个空基线，被整批误判成新发现。这个错误不会报错，界面表现只是
    新发现数突然涌出一大批。"""
    seed = await db.run_sync(_seed)
    with pytest.raises(UnprocessableError) as exc:
        await service.submit_report(db, seed["owner"], _req(asins=()))
    assert exc.value.code == "competitor.report_empty"
    assert await _count(db, "SELECT count(*) FROM analysis.competitor_asin_report") == 0
    assert await _count(db, "SELECT count(*) FROM analysis.competitor_seller_asin") == 0
    assert (
        await _count(
            db,
            "SELECT count(*) FROM analysis.competitor_seller "
            "WHERE seller_id=:s AND site=:t AND baseline_at IS NOT NULL",
            {"s": _SELLER, "t": _SITE},
        )
        == 0
    )


async def test_全部条目格式非法时整批拒绝不落定基线(db):
    """与空列表同一种风险：accepted_rows 为空的两种成因（请求本来就是空的 / 全部被逐条
    拒绝）都不得建立基线，不能只堵其中一种。"""
    seed = await db.run_sync(_seed)
    with pytest.raises(UnprocessableError) as exc:
        await service.submit_report(db, seed["owner"], _req(asins=("不是ASIN", "也不是")))
    assert exc.value.code == "competitor.report_empty"
    assert await _count(db, "SELECT count(*) FROM analysis.competitor_asin_report") == 0
    assert (
        await _count(
            db,
            "SELECT count(*) FROM analysis.competitor_seller "
            "WHERE seller_id=:s AND site=:t AND baseline_at IS NOT NULL",
            {"s": _SELLER, "t": _SITE},
        )
        == 0
    )


async def test_已有基线时空批次同样整批拒绝(db):
    """即便基线已经落定，一次零有效条目的上报本身也不携带任何可用信息，不该被受理——
    这不是"只在首次上报时才检查"的特例规则。"""
    seed = await db.run_sync(_seed)
    await service.submit_report(db, seed["owner"], _req(request_id="rq-1"))
    with pytest.raises(UnprocessableError) as exc:
        await service.submit_report(db, seed["owner"], _req(request_id="rq-2", asins=()))
    assert exc.value.code == "competitor.report_empty"
    assert await _count(db, "SELECT count(*) FROM analysis.competitor_asin_report") == 1


async def test_非法站点码返回422而不是500(db):
    """`normalize_site_code` 抛的是 `UnknownSiteError`，不转译就会被全局兜底处理成 500——
    这是非法输入，不是服务器故障。站点校验在授权检查之前，不需要先建关注关系。"""
    with pytest.raises(UnprocessableError) as exc:
        await service.submit_report(
            db, 1, AsinReportRequest(request_id="rq-1", seller_id=_SELLER, site="UK", items=[])
        )
    assert exc.value.code == "competitor.site_invalid"


# ---------- 基线 ----------


async def test_首次上报落定基线且首批不算新发现(db):
    """不处理的话，导入 2657 家后首刷会瞬间产生约 12 万条假条目，功能当场废掉。"""
    seed = await db.run_sync(_seed)
    res = await service.submit_report(db, seed["owner"], _req(asins=("B0AAAAAAAA", "B0BBBBBBBB")))
    assert res.baseline_established is True
    assert res.new_asins == 2

    # 基线与本批 first_seen_at 相等，而判据是严格大于 —— 故首批一条都不算新发现。
    listed = await _count(
        db,
        "SELECT count(*) FROM analysis.competitor_seller_asin a "
        "JOIN analysis.competitor_seller s USING (seller_id, site) "
        "WHERE a.first_seen_at > s.baseline_at",
    )
    assert listed == 0


async def test_基线一旦落定不再改动(db):
    """改动基线会让基线批次的商品在下一次查询里整批涌进上新流。"""
    seed = await db.run_sync(_seed)
    await service.submit_report(db, seed["owner"], _req(request_id="rq-1"))
    first = await _count(
        db,
        "SELECT extract(epoch from baseline_at)::bigint FROM analysis.competitor_seller "
        "WHERE seller_id=:s",
        {"s": _SELLER},
    )
    res = await service.submit_report(db, seed["owner"], _req(request_id="rq-2"))
    assert res.baseline_established is False
    after = await _count(
        db,
        "SELECT extract(epoch from baseline_at)::bigint FROM analysis.competitor_seller "
        "WHERE seller_id=:s",
        {"s": _SELLER},
    )
    assert first == after


async def test_基线批次的first_seen_at与基线严格相等(db):
    """这是"基线批次不算新发现"能成立的**机制**，值得单独钉住。

    Postgres 的 `now()` 取事务开始时刻（不是语句时刻），而基线与本批 ASIN 落在同一个事务里
    ——两个时刻因此严格相等，配上严格大于的判据，首批恒不进上新流。

    **别改成 `clock_timestamp()`。** 那样 `first_seen_at`（语句时刻）会晚于
    `baseline_at`（事务开始时刻），首批商品反而全部变成"新发现"——正是这条要防的事。
    """
    seed = await db.run_sync(_seed)
    await service.submit_report(db, seed["owner"], _req(asins=("B0AAAAAAAA", "B0BBBBBBBB")))
    equal = await _count(
        db,
        "SELECT count(*) FROM analysis.competitor_seller_asin a "
        "JOIN analysis.competitor_seller s USING (seller_id, site) "
        "WHERE a.first_seen_at = s.baseline_at",
    )
    assert equal == 2


async def test_基线之后的新asin才算新发现(db):
    """跨事务的那一半在 test_跨事务下第二次上报的新品晚于基线 里——本用例只钉计数。

    这里两次上报共用夹具那一个外层事务，`now()` 因此相同；真实链路上一次请求一个事务，
    第二次的 `now()` 严格更晚。所以这条只断言"新增计数正确"，时间序另测。
    """
    seed = await db.run_sync(_seed)
    await service.submit_report(db, seed["owner"], _req(request_id="rq-1", asins=("B0AAAAAAAA",)))
    res = await service.submit_report(
        db, seed["owner"], _req(request_id="rq-2", asins=("B0AAAAAAAA", "B0BBBBBBBB"))
    )
    assert res.new_asins == 1
    assert await _count(db, "SELECT count(*) FROM analysis.competitor_seller_asin") == 2


# ---------- 幂等四态 ----------


async def test_同键同体_回放而不新增(db):
    """超时重发若多出一行上报记录，界面的「最近采集于」会被往前推——数据看起来比实际新，
    而那一列存在的全部意义就是回答"有多新"。"""
    seed = await db.run_sync(_seed)
    first = await service.submit_report(db, seed["owner"], _req(request_id="rq-x"))
    before = await _count(db, "SELECT count(*) FROM analysis.competitor_asin_report")
    latest_before = await _count(
        db,
        "SELECT extract(epoch from max(reported_at))::bigint "
        "FROM analysis.competitor_asin_report",
    )

    replay = await service.submit_report(db, seed["owner"], _req(request_id="rq-x"))

    assert replay.duplicate_of == first.report_id
    assert replay.report_id == first.report_id
    assert await _count(db, "SELECT count(*) FROM analysis.competitor_asin_report") == before
    latest_after = await _count(
        db,
        "SELECT extract(epoch from max(reported_at))::bigint "
        "FROM analysis.competitor_asin_report",
    )
    assert latest_after == latest_before  # 「最近采集于」不被推进


async def test_同键异体_换asin集合返409且一条不写(db):
    """键只能认出"这个键来过"，认不出"这两次是不是同一次上报"。静默回放会把一次真实观测
    丢掉，甚至把另一次上报的计数返回给调用方。"""
    seed = await db.run_sync(_seed)
    await service.submit_report(db, seed["owner"], _req(request_id="rq-x", asins=("B0AAAAAAAA",)))
    rows_before = await _count(db, "SELECT count(*) FROM analysis.competitor_seller_asin")

    with pytest.raises(ConflictError) as exc:
        await service.submit_report(
            db, seed["owner"], _req(request_id="rq-x", asins=("B0CCCCCCCC",))
        )
    assert exc.value.code == "competitor.idempotency_key_reused"
    assert exc.value.http_status == 409
    assert await _count(db, "SELECT count(*) FROM analysis.competitor_seller_asin") == rows_before


async def test_同键异体_换卖家也返409(db):
    seed = await db.run_sync(_seed)
    other_seller = "A16U556F7GIJ50"
    await db.execute(
        text("INSERT INTO analysis.competitor_seller(seller_id,site) VALUES (:s,:t)"),
        {"s": other_seller, "t": _SITE},
    )
    await db.execute(
        text(
            "INSERT INTO analysis.user_seller_watch(user_id,seller_id,site,priority) "
            "VALUES (:u,:s,:t,1)"
        ),
        {"u": seed["owner"], "s": other_seller, "t": _SITE},
    )
    await service.submit_report(db, seed["owner"], _req(request_id="rq-x"))
    with pytest.raises(ConflictError) as exc:
        await service.submit_report(
            db, seed["owner"], _req(request_id="rq-x", seller_id=other_seller)
        )
    assert exc.value.code == "competitor.idempotency_key_reused"


async def test_同键异体_重新采集换了captured_at也返409(db):
    """重新采集是**另一次观测**，本该带新的键——不是重试。真正的重试重发已序列化的原
    payload，captured_at 逐字节相同。把它当重放静默丢掉正是这道锁要防的事。"""
    seed = await db.run_sync(_seed)
    t1 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    await service.submit_report(db, seed["owner"], _req(request_id="rq-x", captured_at=t1))
    with pytest.raises(ConflictError) as exc:
        await service.submit_report(
            db, seed["owner"], _req(request_id="rq-x", captured_at=t1 + timedelta(hours=1))
        )
    assert exc.value.code == "competitor.idempotency_key_reused"


async def test_跨用户同键互不影响(db):
    """跨用户撞键若按全局判定，会让乙的上报被当成重复吞掉，并把甲那次的计数回放给乙——
    既丢数据，又泄露别人的记录。"""
    seed = await db.run_sync(_seed)
    a = await service.submit_report(db, seed["owner"], _req(request_id="same-key"))
    b = await service.submit_report(db, seed["other"], _req(request_id="same-key"))
    assert b.duplicate_of is None
    assert b.report_id != a.report_id
    assert await _count(db, "SELECT count(*) FROM analysis.competitor_asin_report") == 2


async def test_只有展示属性不同仍按重放处理(db):
    """标题被 Amazon 截到不同长度是常态。把它算进摘要会让一次真正的重试撞出假 409。"""
    seed = await db.run_sync(_seed)
    first = await service.submit_report(
        db,
        seed["owner"],
        _req(request_id="rq-x", items=[AsinReportItem(asin="B0AAAAAAAA", title="标题很长的版本")]),
    )
    replay = await service.submit_report(
        db,
        seed["owner"],
        _req(request_id="rq-x", items=[AsinReportItem(asin="B0AAAAAAAA", title="标题短版")]),
    )
    assert replay.duplicate_of == first.report_id


# ---------- 只增不改 ----------


async def test_重复上报不新增行且first_seen_at不变(db):
    """运营每刷新一次页面插件就会再报一次。若每次都更新 first_seen_at，一个老品会因为被
    重新看到而跳进"今天的新发现"。"""
    seed = await db.run_sync(_seed)
    await service.submit_report(db, seed["owner"], _req(request_id="rq-1"))
    seen_before = await _count(
        db,
        "SELECT extract(epoch from first_seen_at)::bigint "
        "FROM analysis.competitor_seller_asin WHERE asin='B0AAAAAAAA'",
    )
    res = await service.submit_report(db, seed["owner"], _req(request_id="rq-2"))
    assert res.new_asins == 0
    assert await _count(db, "SELECT count(*) FROM analysis.competitor_seller_asin") == 1
    seen_after = await _count(
        db,
        "SELECT extract(epoch from first_seen_at)::bigint "
        "FROM analysis.competitor_seller_asin WHERE asin='B0AAAAAAAA'",
    )
    assert seen_before == seen_after


async def test_标题只在首次落定(db):
    seed = await db.run_sync(_seed)
    await service.submit_report(
        db,
        seed["owner"],
        _req(request_id="rq-1", items=[AsinReportItem(asin="B0AAAAAAAA", title="首次见到")]),
    )
    await service.submit_report(
        db,
        seed["owner"],
        _req(request_id="rq-2", items=[AsinReportItem(asin="B0AAAAAAAA", title="后来改了")]),
    )
    title = (
        await db.execute(
            text("SELECT title FROM analysis.competitor_seller_asin WHERE asin='B0AAAAAAAA'")
        )
    ).scalar()
    assert title == "首次见到"


# ---------- 溯源 ----------


async def test_first_report_id指向真正引入它的那次上报(db):
    """共享层进了坏 ASIN 之后，没有这个指针就只能在「全删」与「不管」之间选。"""
    seed = await db.run_sync(_seed)
    r1 = await service.submit_report(db, seed["owner"], _req(request_id="rq-1", asins=("B0AAAAAAAA",)))
    r2 = await service.submit_report(
        db, seed["owner"], _req(request_id="rq-2", asins=("B0AAAAAAAA", "B0BBBBBBBB"))
    )
    rows = dict(
        (
            await db.execute(
                text("SELECT asin, first_report_id FROM analysis.competitor_seller_asin")
            )
        ).all()
    )
    assert rows["B0AAAAAAAA"] == r1.report_id  # 第二次没有改写它
    assert rows["B0BBBBBBBB"] == r2.report_id


# ---------- 时间权威 ----------


async def test_captured_at偏移不影响判定(db):
    """客户端时钟能左右判定的话，一台时间偏了的机器就能把整批老品推到基线之前（整批
    消失）或推到未来（整批涌进新发现），而两种错误在界面上都长得像正常数据。"""
    seed = await db.run_sync(_seed)
    ancient = datetime(2020, 1, 1, tzinfo=UTC)
    await service.submit_report(
        db, seed["owner"], _req(request_id="rq-1", captured_at=ancient)
    )
    row = (
        await db.execute(
            text(
                "SELECT extract(epoch from a.first_seen_at)::bigint > "
                "       extract(epoch from a.captured_at)::bigint "
                "FROM (SELECT first_seen_at, "
                "        (SELECT captured_at FROM analysis.competitor_asin_report LIMIT 1) "
                "        AS captured_at "
                "      FROM analysis.competitor_seller_asin) a"
            )
        )
    ).scalar()
    assert row is True  # first_seen_at 用的是服务端时间，不是 2020 年


async def test_captured_at只落进上报记录不落进在售关系(db):
    seed = await db.run_sync(_seed)
    await service.submit_report(db, seed["owner"], _req(captured_at=datetime(2020, 1, 1, tzinfo=UTC)))
    cols = [
        r[0]
        for r in (
            await db.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='analysis' AND table_name='competitor_seller_asin'"
                )
            )
        ).all()
    ]
    assert "captured_at" not in cols


# ---------- 跨事务：真实链路的时间序 ----------


@pytest.fixture()
async def committing_db():
    """**真的提交**的会话，用完按种子清理。

    上面那个夹具把整场包在一个事务里，`now()` 因此恒定——而"第二次上报的新品晚于基线"
    恰恰依赖两次请求分属不同事务。这个夹具是唯一能验到那件事的办法。

    代价是数据真的落库，所以清理放在 `finally` 里、按种子标识精确删除，失败也不留残渣。
    删除顺序跟着外键走：ASIN 与上报 → 关注 → 卖家 → 用户。
    """
    if not _tables_exist_sync():
        pytest.skip("测试库未迁移，请先运行 alembic upgrade head")
    engine = create_async_engine(_async_url(TEST_URL), poolclass=NullPool)
    session = AsyncSession(bind=engine)
    try:
        yield session
    finally:
        for sql in (
            "DELETE FROM analysis.competitor_seller_asin WHERE seller_id=:s",
            "DELETE FROM analysis.competitor_asin_report WHERE seller_id=:s",
            "DELETE FROM analysis.user_seller_watch WHERE seller_id=:s",
            "DELETE FROM analysis.competitor_seller WHERE seller_id=:s",
            "DELETE FROM analysis.user_center WHERE user_name LIKE 'asin-xtxn-%'",
        ):
            await session.execute(text(sql), {"s": _XTXN_SELLER})
        await session.commit()
        await session.close()
        await engine.dispose()


_XTXN_SELLER = "AXTXNSELLER01"


async def test_跨事务下第二次上报的新品晚于基线(committing_db):
    """真实链路上一次请求一个事务，所以第二次上报的 `now()` 严格晚于基线。

    这是整个功能赖以成立的时间序：**基线批次相等（不算）、后续批次严格更大（算）**。
    在单事务夹具里两者都相等，这条测不出来——而它错了不会报错，只会表现为"上新流永远
    是空的"。
    """
    db = committing_db
    owner = (
        await db.execute(
            text(
                "INSERT INTO analysis.user_center(user_name,hashed_pwd,is_active,is_super) "
                "VALUES ('asin-xtxn-owner','x',true,false) RETURNING id"
            )
        )
    ).scalar()
    await db.execute(
        text("INSERT INTO analysis.competitor_seller(seller_id,site) VALUES (:s,'US')"),
        {"s": _XTXN_SELLER},
    )
    await db.execute(
        text(
            "INSERT INTO analysis.user_seller_watch(user_id,seller_id,site,priority) "
            "VALUES (:u,:s,'US',1)"
        ),
        {"u": owner, "s": _XTXN_SELLER},
    )
    await db.commit()

    def _r(rid, asins):
        return AsinReportRequest(
            request_id=rid,
            seller_id=_XTXN_SELLER,
            site="US",
            items=[AsinReportItem(asin=a) for a in asins],
        )

    # 第一次上报：落基线，本批不算新发现。service 内部 commit，故下一次是新事务。
    await service.submit_report(db, owner, _r("xt-1", ["B0AAAAAAAA"]))
    # 第二次上报：新的 ASIN，其 first_seen_at 应严格晚于基线。
    await service.submit_report(db, owner, _r("xt-2", ["B0AAAAAAAA", "B0BBBBBBBB"]))

    rows = dict(
        (
            await db.execute(
                text(
                    "SELECT a.asin, a.first_seen_at > s.baseline_at "
                    "FROM analysis.competitor_seller_asin a "
                    "JOIN analysis.competitor_seller s USING (seller_id, site) "
                    "WHERE a.seller_id=:s"
                ),
                {"s": _XTXN_SELLER},
            )
        ).all()
    )
    assert rows["B0AAAAAAAA"] is False  # 基线批次：不算
    assert rows["B0BBBBBBBB"] is True   # 基线之后：算


# ---------- 上新流合并两类来源 ----------


async def _seed_dtc(db, user_id, *, domain="mixed-shop.com", n=3):
    """一个已建基线的独立站 + n 个基线之后上架的商品。"""
    base = datetime(2026, 8, 1, tzinfo=UTC)
    await db.execute(
        text(
            "INSERT INTO analysis.competitor_store(domain,name,first_fetch_at,currency) "
            "VALUES (:d,'混合店',:t,'USD')"
        ),
        {"d": domain, "t": base},
    )
    await db.execute(
        text("INSERT INTO analysis.user_store_watch(user_id,domain) VALUES (:u,:d)"),
        {"u": user_id, "d": domain},
    )
    for i in range(n):
        # published_at 与 first_seen_at 都晚于 first_fetch_at，故进流。
        at = base + timedelta(days=i + 1)
        await db.execute(
            text(
                "INSERT INTO analysis.competitor_store_product"
                "(domain,product_id,handle,title,published_at,first_seen_at) "
                "VALUES (:d,:p,:h,:ti,:at,:at)"
            ),
            {"d": domain, "p": 1000 + i, "h": f"h{i}", "ti": f"独立站商品{i}", "at": at},
        )
    return domain


async def _seed_amazon_arrivals(db, user_id, *, n=3):
    """一个已建基线的 Amazon 卖家 + n 个基线之后发现的 ASIN。

    直接写表而不走 service：这里要控制 `first_seen_at` 的具体取值来验排序与翻页，
    而 service 一律用服务端事务时间（那是对的，见 test_captured_at偏移不影响判定）。
    """
    base = datetime(2026, 8, 1, tzinfo=UTC)
    await db.execute(
        text("UPDATE analysis.competitor_seller SET baseline_at=:t WHERE seller_id=:s"),
        {"t": base, "s": _SELLER},
    )
    for i in range(n):
        at = base + timedelta(days=i + 1, hours=12)
        await db.execute(
            text(
                "INSERT INTO analysis.competitor_seller_asin"
                "(seller_id,site,asin,first_seen_at,title) VALUES (:s,:t,:a,:at,:ti)"
            ),
            {"s": _SELLER, "t": _SITE, "a": f"B0MIX{i:05d}", "at": at, "ti": f"卖家商品{i}"},
        )


async def test_同一seller_id跨站点卖同一asin两行都保留不冲突(db):
    """共享层的真实粒度是 (seller_id, site, asin)，不是 (seller_id, asin)。同一 seller_id
    跨站点卖同一 ASIN 是常态；若响应身份或排序键漏了 site，这两行会在 API/前端 rowKey 上
    撞成同一行，翻页时互相顶替。这里用完全相同的 first_seen_at 制造平局，同时验证
    site 参与了次级排序键。"""
    seed = await db.run_sync(_seed)
    same_at = datetime(2026, 8, 2, 12, tzinfo=UTC)
    await _seed_amazon_arrivals(db, seed["owner"], n=1)  # US: B0MIX00000 @ same_at

    gb_site = "GB"
    await db.execute(
        text(
            "INSERT INTO analysis.competitor_seller(seller_id,site,baseline_at) "
            "VALUES (:s,:t,:b)"
        ),
        {"s": _SELLER, "t": gb_site, "b": datetime(2026, 8, 1, tzinfo=UTC)},
    )
    await db.execute(
        text(
            "INSERT INTO analysis.user_seller_watch(user_id,seller_id,site,priority) "
            "VALUES (:u,:s,:t,1)"
        ),
        {"u": seed["owner"], "s": _SELLER, "t": gb_site},
    )
    await db.execute(
        text(
            "INSERT INTO analysis.competitor_seller_asin"
            "(seller_id,site,asin,first_seen_at,title) VALUES (:s,:t,:a,:at,:ti)"
        ),
        {"s": _SELLER, "t": gb_site, "a": "B0MIX00000", "at": same_at, "ti": "GB同款"},
    )

    feed = await service_store.new_arrivals(
        db, seed["owner"], NewArrivalRequest(days=90, per_page=50)
    )
    amazon_rows = [r for r in feed.data if r.source == "AMAZON"]
    assert len(amazon_rows) == 2
    assert {r.site for r in amazon_rows} == {_SITE, gb_site}
    # 两行的 source_key + product_key 完全相同，只有 site 能区分——正是要防的碰撞场景
    assert {(r.source_key, r.product_key) for r in amazon_rows} == {(_SELLER, "B0MIX00000")}

    # 翻页同样不能因为这个平局漏掉或重复任何一行
    seen: list[tuple[str, str, str | None]] = []
    for page in (1, 2):
        page_feed = await service_store.new_arrivals(
            db, seed["owner"], NewArrivalRequest(days=90, page=page, per_page=1)
        )
        seen.extend((r.source, r.product_key, r.site) for r in page_feed.data if r.source == "AMAZON")
    assert len(seen) == len(set(seen)) == 2


async def test_上新流两类来源都在同一个流里(db):
    seed = await db.run_sync(_seed)
    await _seed_dtc(db, seed["owner"])
    await _seed_amazon_arrivals(db, seed["owner"])

    feed = await service_store.new_arrivals(
        db, seed["owner"], NewArrivalRequest(days=90, page=1, per_page=50)
    )
    assert feed.total == 6
    assert {r.source for r in feed.data} == {"DTC", "AMAZON"}


async def test_amazon行的时间语义恒为发现于(db):
    """被动采集证明不了商品何时上架。标成「上架于」等于拿「我们发现得晚」冒充「他上得晚」。"""
    seed = await db.run_sync(_seed)
    await _seed_dtc(db, seed["owner"])
    await _seed_amazon_arrivals(db, seed["owner"])

    feed = await service_store.new_arrivals(
        db, seed["owner"], NewArrivalRequest(days=90, per_page=50)
    )
    kinds = {r.source: {x.time_kind for x in feed.data if x.source == r.source} for r in feed.data}
    assert kinds["AMAZON"] == {"DISCOVERED"}
    assert kinds["DTC"] == {"PUBLISHED"}  # 有数据源给的 published_at


async def test_合并后按显示的时间倒序(db):
    """列表若不按自己显示的那一列排序，读起来就是坏的。"""
    seed = await db.run_sync(_seed)
    await _seed_dtc(db, seed["owner"])
    await _seed_amazon_arrivals(db, seed["owner"])

    feed = await service_store.new_arrivals(
        db, seed["owner"], NewArrivalRequest(days=90, per_page=50)
    )
    times = [r.at for r in feed.data]
    assert times == sorted(times, reverse=True)
    # 两类来源真的交错，不是先排完一类再排另一类
    assert len({r.source for r in feed.data[:3]}) == 2


async def test_翻页无重复无遗漏(db):
    """各来源自己的偏移量与合并后的全局序号对不上时，同一行会在两页各出现一次——
    而界面表现只是「某条上新出现了两次」，极难归因。"""
    seed = await db.run_sync(_seed)
    await _seed_dtc(db, seed["owner"])
    await _seed_amazon_arrivals(db, seed["owner"])

    seen: list[tuple[str, str]] = []
    for page in (1, 2, 3):
        feed = await service_store.new_arrivals(
            db, seed["owner"], NewArrivalRequest(days=90, page=page, per_page=2)
        )
        assert feed.total == 6
        seen.extend((r.source, r.product_key) for r in feed.data)

    assert len(seen) == 6
    assert len(set(seen)) == 6  # 无重复
    # 与一次取全的结果完全一致（无遗漏、次序相同）
    whole = await service_store.new_arrivals(
        db, seed["owner"], NewArrivalRequest(days=90, per_page=50)
    )
    assert seen == [(r.source, r.product_key) for r in whole.data]


async def test_时间相同的多条也能翻完(db):
    """`published_at` 只精确到分钟，同一分钟内多条是常态。

    **这条只验"能翻完且不重复"，验不了次级排序键在不在。** 去掉 ORDER BY 里的次级键，
    本用例照样绿——6 行数据下 Postgres 的顺序碰巧是稳定的，乱序要在更大的数据量或不同的
    执行计划下才现形。次级键的存在由
    `test_competitor_asin_unit.py::test_上新流排序带次级键` 结构性地钉住。
    """
    seed = await db.run_sync(_seed)
    same = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    await db.execute(
        text(
            "INSERT INTO analysis.competitor_store(domain,name,first_fetch_at,currency) "
            "VALUES ('tie-shop.com','平局店',:t,'USD')"
        ),
        {"t": datetime(2026, 8, 1, tzinfo=UTC)},
    )
    await db.execute(
        text("INSERT INTO analysis.user_store_watch(user_id,domain) VALUES (:u,'tie-shop.com')"),
        {"u": seed["owner"]},
    )
    for i in range(4):
        await db.execute(
            text(
                "INSERT INTO analysis.competitor_store_product"
                "(domain,product_id,handle,title,published_at,first_seen_at) "
                "VALUES ('tie-shop.com',:p,:h,:ti,:at,:at)"
            ),
            {"p": 2000 + i, "h": f"t{i}", "ti": f"同刻商品{i}", "at": same},
        )
    await db.execute(
        text("UPDATE analysis.competitor_seller SET baseline_at=:t WHERE seller_id=:s"),
        {"t": datetime(2026, 8, 1, tzinfo=UTC), "s": _SELLER},
    )
    for i in range(2):
        await db.execute(
            text(
                "INSERT INTO analysis.competitor_seller_asin"
                "(seller_id,site,asin,first_seen_at,title) VALUES (:s,:t,:a,:at,:ti)"
            ),
            {"s": _SELLER, "t": _SITE, "a": f"B0TIE{i:05d}", "at": same, "ti": f"同刻卖家品{i}"},
        )

    seen: list[tuple[str, str]] = []
    for page in (1, 2, 3):
        feed = await service_store.new_arrivals(
            db, seed["owner"], NewArrivalRequest(days=90, page=page, per_page=2)
        )
        seen.extend((r.source, r.product_key) for r in feed.data)
    assert len(set(seen)) == 6


async def test_未关注的卖家不进我的上新流(db):
    """流按 user_id 过滤。共享层的数据是所有关注者共用的，但"谁看得到"是私有的。"""
    seed = await db.run_sync(_seed)
    await _seed_amazon_arrivals(db, seed["owner"])
    await db.execute(
        text("DELETE FROM analysis.user_seller_watch WHERE user_id=:u"), {"u": seed["other"]}
    )
    feed = await service_store.new_arrivals(
        db, seed["other"], NewArrivalRequest(days=90, per_page=50)
    )
    assert feed.total == 0


async def test_无基线的卖家一条都不进流(db):
    """没有基线就无从区分「刚开始看」与「新发现」，首刷那批会整批涌进来。"""
    seed = await db.run_sync(_seed)
    await _seed_amazon_arrivals(db, seed["owner"])
    await db.execute(
        text("UPDATE analysis.competitor_seller SET baseline_at=NULL WHERE seller_id=:s"),
        {"s": _SELLER},
    )
    feed = await service_store.new_arrivals(
        db, seed["owner"], NewArrivalRequest(days=90, per_page=50)
    )
    assert feed.total == 0


async def test_没有标题的asin用asin兜底(db):
    """响应契约里 title 必填，而上报可以不带标题。空标题在界面上是一行看不出是什么的条目。"""
    seed = await db.run_sync(_seed)
    await db.execute(
        text("UPDATE analysis.competitor_seller SET baseline_at=:t WHERE seller_id=:s"),
        {"t": datetime(2026, 8, 1, tzinfo=UTC), "s": _SELLER},
    )
    await db.execute(
        text(
            "INSERT INTO analysis.competitor_seller_asin(seller_id,site,asin,first_seen_at) "
            "VALUES (:s,:t,'B0NOTITLE1',:at)"
        ),
        {"s": _SELLER, "t": _SITE, "at": datetime(2026, 8, 5, tzinfo=UTC)},
    )
    feed = await service_store.new_arrivals(
        db, seed["owner"], NewArrivalRequest(days=90, per_page=50)
    )
    assert feed.data[0].title == "B0NOTITLE1"
    # 卖家名我们从未取过，恒为 NULL——界面回落显示 seller_id，不拿用户私有备注顶替
    assert feed.data[0].source_name is None


# ---------- 卖家清单的两个指标列 ----------


async def _watch_row(db, user_id, seller_id=_SELLER):
    from app.table.competitor.watch_schemas import WatchListRequest

    resp = await service_watch.list_watches(db, user_id, WatchListRequest(page=1, perPage=50))
    return next(r for r in resp.data if r.seller_id == seller_id)


async def test_无基线的卖家两个指标都是None而不是0(db):
    """**这是本段唯一容易写错、且错了不会报错的地方。** 相关子查询里的 count(*) 恒返回 0
    而不是 NULL；写成 COALESCE(...,0) 或裸用 count，整个「待建立基线 vs 监控中」的界面
    故事就地塌掉——0 会被读成"这家店没有新发现"，而事实是"这个卖家还没有被采集过"。"""
    seed = await db.run_sync(_seed)
    row = await _watch_row(db, seed["owner"])
    assert row.status == "PENDING_BASELINE"
    assert row.new_discoveries_7d is None
    assert row.last_reported_at is None


async def test_有基线但窗口内无新发现时是真实的0(db):
    """有基线时 0 的含义才是「已采集范围内没有新发现」，此时必须是数字不是占位符。"""
    seed = await db.run_sync(_seed)
    await service.submit_report(db, seed["owner"], _req())  # 首批，恒不算新发现

    row = await _watch_row(db, seed["owner"])
    assert row.status == "MONITORING"
    assert row.new_discoveries_7d == 0
    assert row.last_reported_at is not None


async def test_窗口内的新发现被计入(db):
    seed = await db.run_sync(_seed)
    await db.execute(
        text("UPDATE analysis.competitor_seller SET baseline_at=:t WHERE seller_id=:s"),
        {"t": datetime.now(UTC) - timedelta(days=30), "s": _SELLER},
    )
    for i, days_ago in enumerate((1, 3, 20)):  # 第三条落在 7 天窗口外
        await db.execute(
            text(
                "INSERT INTO analysis.competitor_seller_asin"
                "(seller_id,site,asin,first_seen_at) VALUES (:s,:t,:a,:at)"
            ),
            {
                "s": _SELLER,
                "t": _SITE,
                "a": f"B0WIN{i:05d}",
                "at": datetime.now(UTC) - timedelta(days=days_ago),
            },
        )
    row = await _watch_row(db, seed["owner"])
    assert row.new_discoveries_7d == 2


async def test_基线批次不计入新发现数(db):
    """与上新流同构：判据是严格大于基线，首批与基线时刻相等故不算。"""
    seed = await db.run_sync(_seed)
    await service.submit_report(db, seed["owner"], _req(asins=("B0AAAAAAAA", "B0BBBBBBBB")))
    row = await _watch_row(db, seed["owner"])
    assert row.new_discoveries_7d == 0


async def test_最近采集于取最新一次上报(db):
    """「很久没采过」与「确实没有新发现」在界面上长得一模一样，缺了这一列就分不开。"""
    seed = await db.run_sync(_seed)
    await service.submit_report(db, seed["owner"], _req(request_id="rq-1"))
    await service.submit_report(db, seed["owner"], _req(request_id="rq-2"))
    row = await _watch_row(db, seed["owner"])
    # 两点都是踩出来的：
    # 1. **直接比时间戳，不要绕 epoch 整数**。Python 的 int() 截断、Postgres 的
    #    extract(epoch)::bigint 四舍五入，亚秒部分 ≥ .5 时差 1 秒——单跑碰巧绿、全量红。
    # 2. **取值必须带作用域**。裸 max() 只在"库里除了我什么都没有"时成立。
    latest = (
        await db.execute(
            text(
                "SELECT max(reported_at) FROM analysis.competitor_asin_report "
                "WHERE seller_id=:s AND site=:t AND user_id=:u"
            ),
            {"s": _SELLER, "t": _SITE, "u": seed["owner"]},
        )
    ).scalar()
    assert row.last_reported_at == latest


async def test_别人的上报也算数指标是共享的(db):
    """在售数据是共享层：甲采的，乙只要也关注了这个卖家就该看到。抓取代价按 unique
    seller 花，一个卖家被十个人关注也只该被采一次——那正是分共享层的全部意义。"""
    seed = await db.run_sync(_seed)
    await service.submit_report(db, seed["owner"], _req())
    row = await _watch_row(db, seed["other"])
    assert row.status == "MONITORING"
    assert row.new_discoveries_7d == 0  # 是 0 不是 None：这个卖家已经有基线了
