"""独立站监控 API 的集成测试。

默认跳过，只在设置 TEST_DATABASE_URL 时运行。

    TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run pytest test/test_dtc_store_api_integration.py -v

这里钉的是四件事：**加入时就判定非 Shopify**（不留到第一次拉取伪装成抓取失败）、
**跨用户一律 404 而非 403**、**上新流排除首刷那批**、**近 7 日上新数按 published_at
算所以首刷当天即准确**。
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.common.errors import BadRequestError, ConflictError, NotFoundError
from app.table.competitor import store_fetcher
from app.table.competitor import store_service as service
from app.table.competitor.store_schemas import (
    STATUS_NEVER_FETCHED,
    NewArrivalRequest,
    ProductListRequest,
    StoreAddRequest,
    StoreListRequest,
    StoreUpdateRequest,
)

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
DOMAIN = "dtc-api-store.example"


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture()
async def db():
    engine = create_async_engine(_async_url(TEST_URL), poolclass=NullPool)
    connection = await engine.connect()
    trans = await connection.begin()
    session = AsyncSession(bind=connection, join_transaction_mode="create_savepoint")
    try:
        if await session.scalar(text("SELECT to_regclass('analysis.competitor_store')")) is None:
            pytest.skip("测试库未迁移，请先运行 alembic upgrade head")
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await connection.close()
        await engine.dispose()


def _seed_users(db):
    def _user(name):
        return db.execute(
            text(
                "INSERT INTO analysis.user_center(user_name,hashed_pwd,is_active,is_super) "
                "VALUES (:n,'x',true,false) RETURNING id"
            ),
            {"n": name},
        ).scalar()

    return {"owner": _user("dtc-owner"), "other": _user("dtc-other")}


_META = {
    "id": 1,
    "name": "示例店",
    "currency": "GBP",
    "country": "CN",
    "myshopify_domain": "x.myshopify.com",
    "published_products_count": 3,
    "ships_to_countries": ["GB"],
}


def _stub_client(handler):
    """把 service 用的 AsyncClient 换成假 transport。

    换 client 而不是换 ``probe_store_meta``，是为了让探测逻辑本身也被测到；顺带避开
    真实 client 会读取环境里的代理设置（本机有 SOCKS 代理，构造即报缺 socksio）。
    """

    def _factory():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return _factory


def _meta_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text=json.dumps(_META))


@pytest.fixture(autouse=True)
def stub_probe(monkeypatch):
    """默认返回一个合法的 Shopify meta，不出网。"""
    monkeypatch.setattr(service, "_store_client", _stub_client(_meta_response))
    return monkeypatch


class _AsyncioWithRecordedSleep:
    """store_fetcher 看到的 ``asyncio``：只有 ``sleep`` 换成记账的空操作，其余照旧透传。"""

    def __init__(self, sleeps: list[float]):
        async def _sleep(seconds):
            sleeps.append(seconds)

        self.sleep = _sleep

    def __getattr__(self, name):
        return getattr(asyncio, name)


@pytest.fixture(autouse=True)
def backoff_sleeps(monkeypatch):
    """探测失败会按 1.5s×(n+1) 退避重试，「打不开」类用例因此各空等 4.5s。

    重试逻辑照常走，只把等待换成空操作并记下每次退避时长，供用例断言重试确实发生。
    不改 ``_RETRY_BACKOFF_SECONDS``：它被绑成了函数默认参数，运行期改模块常量不生效。
    """
    sleeps: list[float] = []
    monkeypatch.setattr(store_fetcher, "asyncio", _AsyncioWithRecordedSleep(sleeps))
    return sleeps


async def _add(db, user_id, domain=DOMAIN, **kw):
    return await service.add_store(db, user_id, StoreAddRequest(domain=domain, **kw))


# ---------- 加入 ----------


async def test_加入时规范化域名并回写币种(db):
    users = await db.run_sync(_seed_users)
    out = await _add(db, users["owner"], domain="https://WWW." + DOMAIN + "/collections/all")

    assert out.domain == DOMAIN
    assert out.currency == "GBP"
    assert out.name == "示例店"
    # 还没拉过，状态必须是「从未拉取」而不是「拉取失败」——新加的站点不是坏了
    assert out.status == STATUS_NEVER_FETCHED
    assert out.new_in_7d == 0


async def test_非Shopify站点在加入这一步就被拒绝(db, stub_probe):
    """不留到第一次拉取——否则它会以「抓取失败」的面目出现，用户会去重试、去等。"""
    users = await db.run_sync(_seed_users)

    def _not_shopify(request):
        return httpx.Response(200, text=json.dumps({"hello": "world"}))

    stub_probe.setattr(service, "_store_client", _stub_client(_not_shopify))

    with pytest.raises(BadRequestError) as exc:
        await _add(db, users["owner"])
    assert "Shopify" in exc.value.message


async def test_站点打不开与非Shopify是两种拒绝(db, stub_probe, backoff_sleeps):
    users = await db.run_sync(_seed_users)

    def _unreachable(request):
        raise httpx.ConnectTimeout("boom")

    stub_probe.setattr(service, "_store_client", _stub_client(_unreachable))

    with pytest.raises(BadRequestError) as exc:
        await _add(db, users["owner"])
    assert "无法访问" in exc.value.message
    # 瞬时故障要重试满 _RETRIES 次，每次退避线性递增（1.5s、3s）
    assert backoff_sleeps == [
        store_fetcher._RETRY_BACKOFF_SECONDS * n for n in range(1, store_fetcher._RETRIES + 1)
    ]


async def test_粘贴亚马逊链接给出去处而不是笼统报错(db):
    users = await db.run_sync(_seed_users)
    with pytest.raises(BadRequestError) as exc:
        await _add(db, users["owner"], domain="https://www.amazon.com/s?me=A1TZ946F8HZTNY")
    assert "亚马逊" in exc.value.message


async def test_重复关注冲突(db):
    users = await db.run_sync(_seed_users)
    await _add(db, users["owner"])
    with pytest.raises(ConflictError):
        await _add(db, users["owner"])


async def test_两个用户可以关注同一个站点且互不可见(db):
    """站点主体全局共享（抓取只做一次），关注关系账号私有。"""
    users = await db.run_sync(_seed_users)
    await _add(db, users["owner"])
    await _add(db, users["other"])

    only_one = await db.scalar(
        text("SELECT count(*) FROM analysis.competitor_store WHERE domain=:d"), {"d": DOMAIN}
    )
    assert only_one == 1

    listing = await service.list_stores(db, users["owner"], StoreListRequest())
    assert [s.domain for s in listing.data] == [DOMAIN]


# ---------- 归属 ----------


async def test_别人的站点一律404而非403(db):
    """403 等于确认了它存在，而这里连存在与否都不该泄露。"""
    users = await db.run_sync(_seed_users)
    await _add(db, users["owner"])

    with pytest.raises(NotFoundError):
        await service.update_store_watch(
            db, users["other"], DOMAIN, StoreUpdateRequest(note="x")
        )
    with pytest.raises(NotFoundError):
        await service.remove_store_watch(db, users["other"], DOMAIN)
    with pytest.raises(NotFoundError):
        await service.list_products(db, users["other"], ProductListRequest(domain=DOMAIN))


async def test_取关只删关注关系不删站点与数据(db):
    users = await db.run_sync(_seed_users)
    await _add(db, users["owner"])
    await service.remove_store_watch(db, users["owner"], DOMAIN)

    assert await db.scalar(
        text("SELECT count(*) FROM analysis.competitor_store WHERE domain=:d"), {"d": DOMAIN}
    ) == 1
    listing = await service.list_stores(db, users["owner"], StoreListRequest())
    assert listing.total == 0


# ---------- 上新流与近 7 日 ----------


async def _seed_products(db, *, baseline_at, rows):
    await db.execute(
        text("UPDATE analysis.competitor_store SET baseline_at=:f WHERE domain=:d"),
        {"f": baseline_at, "d": DOMAIN},
    )
    for pid, published, first_seen in rows:
        await db.execute(
            text(
                "INSERT INTO analysis.competitor_store_product"
                "(domain,product_id,handle,title,published_at,first_seen_at) "
                "VALUES (:d,:p,:h,:t,:pub,:fs)"
            ),
            {"d": DOMAIN, "p": pid, "h": f"h{pid}", "t": f"商品{pid}", "pub": published,
             "fs": first_seen},
        )


async def test_上新流排除基线前那批(db):
    """基线前那批是「我们刚开始看」不是「今天上新」。没有 baseline_at 就无从区分。"""
    users = await db.run_sync(_seed_users)
    await _add(db, users["owner"])
    baseline = datetime.now(UTC) - timedelta(days=1)
    await _seed_products(
        db,
        baseline_at=baseline,
        rows=[
            (1, baseline - timedelta(days=3), baseline),          # 首刷那批
            (2, baseline - timedelta(days=2), baseline),          # 首刷那批
            (3, datetime.now(UTC), datetime.now(UTC)),            # 之后真的上新
        ],
    )

    feed = await service.new_arrivals(db, users["owner"], NewArrivalRequest(days=30))

    assert feed.total == 1
    assert feed.data[0].product_key == "3"
    # 独立站有真实上架时间，故标为「上架于」而不是「发现于」
    assert feed.data[0].time_kind == "PUBLISHED"
    assert feed.data[0].source == "DTC"


async def test_近7日上新数按published_at算所以首刷当天即准确(db):
    """按 first_seen_at 算的话，刚纳入的站点会把整个存量目录报成「近 7 日上新」。"""
    users = await db.run_sync(_seed_users)
    await _add(db, users["owner"])
    now = datetime.now(UTC)
    await _seed_products(
        db,
        baseline_at=now,
        rows=[
            (1, now - timedelta(days=2), now),    # 近 7 日内上架
            (2, now - timedelta(days=40), now),   # 很久以前上架
        ],
    )

    listing = await service.list_stores(db, users["owner"], StoreListRequest())

    assert listing.data[0].new_in_7d == 1
    assert listing.data[0].product_count == 2


async def test_商品清单带币种(db):
    """价格数字本身不带币种，分开取迟早会拿 A 店的币种显示 B 店的价格。"""
    users = await db.run_sync(_seed_users)
    await _add(db, users["owner"])
    await _seed_products(
        db, baseline_at=datetime.now(UTC), rows=[(1, datetime.now(UTC), datetime.now(UTC))]
    )

    result = await service.list_products(db, users["owner"], ProductListRequest(domain=DOMAIN))

    assert result.currency == "GBP"
    assert result.total == 1


async def test_商品清单的include是行集合维度(db):
    users = await db.run_sync(_seed_users)
    await _add(db, users["owner"])
    now = datetime.now(UTC)
    await _seed_products(db, baseline_at=now, rows=[(1, now, now), (2, now, now)])
    await db.execute(
        text(
            "UPDATE analysis.competitor_store_product SET delisted_at=now() "
            "WHERE domain=:d AND product_id=2"
        ),
        {"d": DOMAIN},
    )

    listed = await service.list_products(db, users["owner"], ProductListRequest(domain=DOMAIN))
    delisted = await service.list_products(
        db, users["owner"], ProductListRequest(domain=DOMAIN, include="DELISTED")
    )
    both = await service.list_products(
        db, users["owner"], ProductListRequest(domain=DOMAIN, include="ALL")
    )

    assert (listed.total, delisted.total, both.total) == (1, 1, 2)


async def test_商品详情的价格与名次历史按时间倒序(db):
    """详情页要看的是"最近发生了什么"，不是从头翻起（PR #74 审核 P1）。

    `price_history_stmt`/`rank_history_stmt` 一度是升序，前端原样渲染会把最早的一次
    调价排在最上面——这条钉住排序方向，不依赖前端再排一次。
    """
    users = await db.run_sync(_seed_users)
    await _add(db, users["owner"])
    now = datetime.now(UTC)
    await _seed_products(db, baseline_at=now, rows=[(1, now, now)])

    older = now - timedelta(days=2)
    newer = now - timedelta(hours=1)
    for observed_at, price in ((older, "8.99"), (newer, "12.99")):
        await db.execute(
            text(
                "INSERT INTO analysis.competitor_store_price"
                "(domain,variant_id,observed_at,product_id,sku,price,compare_at_price,available) "
                "VALUES (:d,1,:obs,1,'SKU-A',:price,NULL,true)"
            ),
            {"d": DOMAIN, "obs": observed_at, "price": price},
        )
    for captured_on, rank in ((older.date(), 50), (newer.date(), 12)):
        await db.execute(
            text(
                "INSERT INTO analysis.competitor_store_rank(domain,captured_on,rank,product_id) "
                "VALUES (:d,:c,:r,1)"
            ),
            {"d": DOMAIN, "c": captured_on, "r": rank},
        )

    result = await service.product_history(db, users["owner"], DOMAIN, 1)

    # 最新的一条排第一——价格与名次两个列表都要满足
    assert [str(p.price) for p in result.prices] == ["12.99", "8.99"]
    assert [r.rank for r in result.ranks] == [12, 50]


async def test_分组可同时装卖家与独立站(db):
    """分组是纯标签，MUST NOT 因成员类型分裂成两套分组体系。"""
    users = await db.run_sync(_seed_users)
    group_id = await db.scalar(
        text(
            "INSERT INTO analysis.user_seller_group(user_id,name) "
            "VALUES (:u,'头部竞对') RETURNING group_id"
        ),
        {"u": users["owner"]},
    )
    out = await _add(db, users["owner"], group_id=group_id)

    assert out.group_id == group_id
    assert out.group_name == "头部竞对"


# ---------- 批量加入 ----------


def _routed(handler_map):
    """按域名分发的假 transport：域名 -> 处理函数。未列出的域名一律当作非 Shopify。"""

    def handler(request: httpx.Request) -> httpx.Response:
        fn = handler_map.get(request.url.host)
        if fn is None:
            return httpx.Response(200, text=json.dumps({"hello": "world"}))
        return fn(request)

    return _stub_client(handler)


def _ok_meta(name: str):
    def _fn(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=json.dumps({**_META, "name": name}))

    return _fn


def _boom(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectTimeout("boom")


async def _batch(db, user_id, domains, **kw):
    from app.table.competitor.store_schemas import StoreBatchAddRequest

    return await service.add_stores_batch(
        db, user_id, StoreBatchAddRequest(domains=domains, **kw)
    )


async def test_批量里三类结果各归各段且拒绝逐条附原因(db, stub_probe):
    """只报「N 条失败」等于让用户自己去猜是哪几条、为什么。"""
    users = await db.run_sync(_seed_users)
    stub_probe.setattr(
        service,
        "_store_client",
        _routed({"good.example": _ok_meta("好店"), "down.example": _boom}),
    )

    result = await _batch(
        db,
        users["owner"],
        [
            "https://www.good.example/collections/all",  # 正常
            "notshopify.example",                         # 非 Shopify
            "down.example",                               # 打不开
            "不是域名",                                    # 域名不合法
            "https://www.amazon.com/s?me=A1TZ946F8HZTNY",  # 亚马逊链接
        ],
    )

    assert [a.domain for a in result.added] == ["good.example"]
    assert result.added[0].name == "好店"
    # 回指的是用户粘的那一行原文，不是规范化后的域名
    assert result.added[0].input == "https://www.good.example/collections/all"

    reasons = {r.input: r.reason for r in result.rejected}
    assert len(reasons) == 4
    assert "Shopify" in reasons["notshopify.example"]
    assert "无法访问" in reasons["down.example"]
    assert "域名" in reasons["不是域名"]
    assert "亚马逊" in reasons["https://www.amazon.com/s?me=A1TZ946F8HZTNY"]
    # 四条原因各不相同——合并成一句就没法判断哪些该换地址重试、哪些根本不受支持
    assert len(set(reasons.values())) == 4


async def test_全部被拒时请求仍然成功返回(db, stub_probe):
    """那不是错误，是「这一批都不受支持」这个事实。"""
    users = await db.run_sync(_seed_users)
    stub_probe.setattr(service, "_store_client", _routed({}))

    result = await _batch(db, users["owner"], ["a.example", "b.example"])

    assert result.added == []
    assert len(result.rejected) == 2
    assert result.success is True


async def test_同批重复域名只处理一次(db, stub_probe):
    users = await db.run_sync(_seed_users)
    stub_probe.setattr(service, "_store_client", _routed({"dup.example": _ok_meta("店")}))

    result = await _batch(
        db,
        users["owner"],
        ["dup.example", "https://www.dup.example/", "HTTPS://DUP.EXAMPLE/products/x?utm=1"],
    )

    assert len(result.added) == 1
    assert await db.scalar(
        text("SELECT count(*) FROM analysis.user_store_watch WHERE domain='dup.example'")
    ) == 1


async def test_已关注的进已关注段不重复写(db, stub_probe):
    users = await db.run_sync(_seed_users)
    stub_probe.setattr(service, "_store_client", _routed({DOMAIN: _ok_meta("已有")}))
    await _add(db, users["owner"])

    result = await _batch(db, users["owner"], [DOMAIN, "x.example"])

    assert [e.domain for e in result.already_watched] == [DOMAIN]
    assert result.added == []


async def test_超条数上限整批拒绝且一条都不写(db, stub_probe):
    """截断会让用户以为全部加进去了。"""
    from app.common.errors import UnprocessableError
    from config import settings

    users = await db.run_sync(_seed_users)
    stub_probe.setattr(service, "_store_client", _routed({}))
    original = settings.COMPETITOR_MAX_WATCH_PER_USER
    settings.COMPETITOR_MAX_WATCH_PER_USER = 2
    try:
        with pytest.raises(UnprocessableError):
            await _batch(db, users["owner"], ["a.example", "b.example", "c.example"])
    finally:
        settings.COMPETITOR_MAX_WATCH_PER_USER = original

    assert await db.scalar(
        text("SELECT count(*) FROM analysis.user_store_watch WHERE user_id=:u"),
        {"u": users["owner"]},
    ) == 0


async def test_批量与单条对同一个脏链接得到相同结果(db, stub_probe):
    """两份规范化实现分叉的表现是「单个粘贴能进，放进批量里同样的链接却被拒」。"""
    users = await db.run_sync(_seed_users)
    dirty = "HTTPS://WWW.Same.Example/products/abc?utm_source=fb#top"
    stub_probe.setattr(service, "_store_client", _routed({"same.example": _ok_meta("同一家")}))

    batch_result = await _batch(db, users["owner"], [dirty])
    assert [a.domain for a in batch_result.added] == ["same.example"]

    # 单条路径对同一个脏链接必须收敛到同一个域名（这里换个用户避免撞重复关注）
    single = await _add(db, users["other"], domain=dirty)
    assert single.domain == "same.example"


async def test_批量可以指定分组(db, stub_probe):
    users = await db.run_sync(_seed_users)
    group_id = await db.scalar(
        text(
            "INSERT INTO analysis.user_seller_group(user_id,name) "
            "VALUES (:u,'批量组') RETURNING group_id"
        ),
        {"u": users["owner"]},
    )
    stub_probe.setattr(service, "_store_client", _routed({"g.example": _ok_meta("店")}))

    await _batch(db, users["owner"], ["g.example"], group_id=group_id)

    assert await db.scalar(
        text("SELECT group_id FROM analysis.user_store_watch WHERE domain='g.example'")
    ) == group_id


async def test_能访问但不是Shopify_不得报成无法访问(db, stub_probe):
    """真实站点对 /meta.json 返回 404 是最常见的「不是 Shopify」形态。

    归进「无法访问」的表现是用户对着一个好好的站点反复重试——实测 baidu.com 就是这样
    被误判过一次。
    """
    users = await db.run_sync(_seed_users)

    def _not_found(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="<html>404</html>")

    stub_probe.setattr(service, "_store_client", _stub_client(_not_found))

    # 单条路径
    with pytest.raises(BadRequestError) as exc:
        await _add(db, users["owner"])
    assert "Shopify" in exc.value.message
    assert "无法访问" not in exc.value.message

    # 批量路径必须给出同一个分类——两边分叉会让同一站点在两条入口得到不同理由
    result = await _batch(db, users["owner"], ["other.example"])
    assert len(result.rejected) == 1
    assert "Shopify" in result.rejected[0].reason


async def test_真的够不着才报无法访问(db, stub_probe):
    users = await db.run_sync(_seed_users)
    stub_probe.setattr(service, "_store_client", _routed({DOMAIN: _boom}))

    result = await _batch(db, users["owner"], [DOMAIN])

    assert "无法访问" in result.rejected[0].reason


# ---------- 商品当前状态口径（change fix-dtc-catalog-collection D5） ----------


async def _price(db, *, variant_id, observed_at, available, product_id=1, price="9.99"):
    await db.execute(
        text(
            "INSERT INTO analysis.competitor_store_price"
            "(domain,variant_id,observed_at,product_id,sku,price,compare_at_price,available) "
            "VALUES (:d,:v,:obs,:p,NULL,:price,NULL,:a)"
        ),
        {"d": DOMAIN, "v": variant_id, "obs": observed_at, "p": product_id, "price": price,
         "a": available},
    )


async def test_一个变体售罄不让整个商品显示为不可购买(db):
    """原先取「最近变动的那一个变体」的 available——偏偏最近变动的就是那个售罄的。"""
    users = await db.run_sync(_seed_users)
    await _add(db, users["owner"])
    now = datetime.now(UTC)
    await _seed_products(db, baseline_at=now, rows=[(1, now, now), (2, now, now)])
    earlier = now - timedelta(days=3)
    for vid in (11, 12, 13):
        await _price(db, variant_id=vid, observed_at=earlier, available=True)
    await _price(db, variant_id=13, observed_at=now, available=False)  # 最近一次变动：售罄
    await _price(db, variant_id=21, observed_at=earlier, available=True, product_id=2)
    await _price(db, variant_id=21, observed_at=now, available=False, product_id=2)

    result = await service.list_products(db, users["owner"], ProductListRequest(domain=DOMAIN))
    by_id = {p.product_id: p for p in result.data}

    assert by_id[1].available is True
    assert by_id[2].available is False


async def test_当前名次只取最新快照_跌出榜单显示未上榜(db):
    users = await db.run_sync(_seed_users)
    await _add(db, users["owner"])
    now = datetime.now(UTC)
    await _seed_products(db, baseline_at=now, rows=[(1, now, now), (2, now, now)])
    two_weeks_ago, today = (now - timedelta(days=14)).date(), now.date()
    for captured_on, rank, pid in ((two_weeks_ago, 12, 1), (two_weeks_ago, 30, 2), (today, 5, 2)):
        await db.execute(
            text(
                "INSERT INTO analysis.competitor_store_rank(domain,captured_on,rank,product_id) "
                "VALUES (:d,:c,:r,:p)"
            ),
            {"d": DOMAIN, "c": captured_on, "r": rank, "p": pid},
        )

    result = await service.list_products(db, users["owner"], ProductListRequest(domain=DOMAIN))
    by_id = {p.product_id: p for p in result.data}

    assert by_id[1].latest_rank is None
    assert by_id[2].latest_rank == 5
