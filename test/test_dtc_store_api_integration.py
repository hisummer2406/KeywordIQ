"""独立站监控 API 的集成测试。

默认跳过，只在设置 TEST_DATABASE_URL 时运行。

    TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run pytest test/test_dtc_store_api_integration.py -v

这里钉的是四件事：**加入时就判定非 Shopify**（不留到第一次拉取伪装成抓取失败）、
**跨用户一律 404 而非 403**、**上新流排除首刷那批**、**近 7 日上新数按 published_at
算所以首刷当天即准确**。
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.common.errors import BadRequestError, ConflictError, NotFoundError
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


async def test_站点打不开与非Shopify是两种拒绝(db, stub_probe):
    users = await db.run_sync(_seed_users)

    def _unreachable(request):
        raise httpx.ConnectTimeout("boom")

    stub_probe.setattr(service, "_store_client", _stub_client(_unreachable))

    with pytest.raises(BadRequestError) as exc:
        await _add(db, users["owner"])
    assert "无法访问" in exc.value.message


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


async def _seed_products(db, *, first_fetch_at, rows):
    await db.execute(
        text("UPDATE analysis.competitor_store SET first_fetch_at=:f WHERE domain=:d"),
        {"f": first_fetch_at, "d": DOMAIN},
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


async def test_上新流排除首刷那批(db):
    """首刷那批是「我们刚开始看」不是「今天上新」。没有 first_fetch_at 就无从区分。"""
    users = await db.run_sync(_seed_users)
    await _add(db, users["owner"])
    baseline = datetime.now(UTC) - timedelta(days=1)
    await _seed_products(
        db,
        first_fetch_at=baseline,
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
        first_fetch_at=now,
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
        db, first_fetch_at=datetime.now(UTC), rows=[(1, datetime.now(UTC), datetime.now(UTC))]
    )

    result = await service.list_products(db, users["owner"], ProductListRequest(domain=DOMAIN))

    assert result.currency == "GBP"
    assert result.total == 1


async def test_商品清单的include是行集合维度(db):
    users = await db.run_sync(_seed_users)
    await _add(db, users["owner"])
    now = datetime.now(UTC)
    await _seed_products(db, first_fetch_at=now, rows=[(1, now, now), (2, now, now)])
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
