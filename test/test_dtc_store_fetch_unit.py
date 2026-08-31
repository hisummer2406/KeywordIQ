"""独立站拉取层单元测试：用 ``httpx.MockTransport`` 喂固定样本，不出网、不连库。

重点全在**完整性判定**上——它是下架判定的唯一前提，判错的后果是把整店判成清仓。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from app.table.competitor.store_fetcher import (
    FETCH_FAILED,
    FETCH_INCOMPLETE,
    FETCH_NO_CATALOG_API,
    FETCH_OK,
    fetch_store,
)

FIXTURES = Path(__file__).parent / "fixtures" / "dtc"
DOMAIN = "example.com"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _meta_with_count(count: int | None) -> str:
    payload = json.loads(_fixture("meta.json"))
    if count is None:
        payload.pop("published_products_count", None)
    else:
        payload["published_products_count"] = count
    return json.dumps(payload)


def _routes(
    *,
    catalog_pages: dict[int, str] | None = None,
    meta: str | None = None,
    rank: str | None = None,
):
    """按路径分发的假路由。缺省给一页商品 + 一页畅销榜 + 之后的空页。"""
    catalog_pages = catalog_pages or {1: _fixture("products_page1.json")}
    meta = meta if meta is not None else _meta_with_count(None)
    rank = rank if rank is not None else _fixture("bestselling_page1.html")

    def handler(request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, request.url.params
        if path == "/meta.json":
            return httpx.Response(200, text=meta)
        if path == "/products.json":
            page = int(params.get("page", "1"))
            return httpx.Response(200, text=catalog_pages.get(page, _fixture("products_empty_page.json")))
        if path == "/collections/all":
            page = int(params.get("page", "1"))
            # 第 2 页起返回空榜，模拟「榜单到头了」
            return httpx.Response(
                200, text=rank if page == 1 else _fixture("bestselling_no_products.html")
            )
        return httpx.Response(404, text="not found")

    return handler


async def _run(handler, **kwargs):
    async with _client(handler) as client:
        return await fetch_store(
            client,
            DOMAIN,
            max_pages=kwargs.pop("max_pages", 40),
            rank_top_n=kwargs.pop("rank_top_n", 100),
            delay_ms=0,
            retry_backoff_seconds=0,
        )


async def test_fetch_store_happy_path_is_complete():
    outcome = await _run(_routes())

    assert outcome.status == FETCH_OK
    assert outcome.catalog_complete is True
    assert outcome.products_json_ok is True
    assert len(outcome.products) == 4
    assert outcome.rank_ok is True
    assert outcome.rank_handles == tuple(json.loads(_fixture("bestselling_expected_order.json")))


async def test_fetch_store_paginates_until_empty_page():
    pages = {1: _fixture("products_page1.json"), 2: _fixture("products_page1.json")}
    outcome = await _run(_routes(catalog_pages=pages))

    # 两页各 4 个商品，第 3 页空 → 自然终止
    assert len(outcome.products) == 8
    assert outcome.catalog_complete is True


async def test_fetch_store_hitting_page_cap_is_incomplete():
    """撞上页数上限仍有内容 = 不完整。

    当成正常结束的话，上限之外的商品会被安静地判成下架。
    """
    always_full = {n: _fixture("products_page1.json") for n in range(1, 10)}
    outcome = await _run(_routes(catalog_pages=always_full), max_pages=3)

    assert outcome.catalog_complete is False
    assert outcome.status == FETCH_INCOMPLETE
    assert "上限" in outcome.error
    # 不完整不等于不写入：见到的商品照常返回，只是不做下架判定
    assert len(outcome.products) == 12


async def test_fetch_store_short_of_declared_count_is_incomplete():
    """meta 声称的商品总数是比「翻到空页」硬得多的完整性校验。"""
    outcome = await _run(_routes(meta=_meta_with_count(999)))

    assert outcome.catalog_complete is False
    assert outcome.status == FETCH_INCOMPLETE
    assert "999" in outcome.error


async def test_fetch_store_more_than_declared_count_stays_complete():
    """多出来可能是拉取期间商户又发布了商品，不算问题。"""
    outcome = await _run(_routes(meta=_meta_with_count(1)))

    assert outcome.catalog_complete is True
    assert outcome.status == FETCH_OK


async def test_fetch_store_catalog_api_404_is_its_own_status():
    """404 是商户关掉端点的**最常见形态**，比「200 但返回 HTML」常见得多。

    报成抓取失败的话，用户会看到「连续 N 次失败」并以为是暂时故障在等，而实际上这个
    端点永久没有了。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/meta.json":
            return httpx.Response(200, text=_meta_with_count(None))
        return httpx.Response(404, text="<html>Not Found</html>")

    outcome = await _run(handler)

    assert outcome.products_json_ok is False
    assert outcome.status == FETCH_NO_CATALOG_API
    assert outcome.catalog_complete is False


async def test_fetch_store_catalog_api_returning_html_is_its_own_status():
    """另一种形态：200 但返回店铺主页 HTML 而不是 JSON。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/meta.json":
            return httpx.Response(200, text=_meta_with_count(None))
        return httpx.Response(200, text="<!doctype html><html><body>Shop</body></html>")

    outcome = await _run(handler)

    assert outcome.products_json_ok is False
    assert outcome.status == FETCH_NO_CATALOG_API


async def test_fetch_store_does_not_retry_client_errors():
    """4xx 是确定的答复不是抖动，重试它只会把一次答复拖成三倍等待。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/meta.json":
            return httpx.Response(200, text=_meta_with_count(None))
        return httpx.Response(404, text="nope")

    await _run(handler)

    assert calls.count("/products.json") == 1


async def test_fetch_store_network_failure_is_not_complete():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/meta.json":
            return httpx.Response(200, text=_meta_with_count(None))
        raise httpx.ConnectTimeout("boom")

    outcome = await _run(handler)

    assert outcome.status == FETCH_FAILED
    assert outcome.catalog_complete is False


async def test_fetch_store_rank_failure_does_not_block_catalog():
    """名次与目录的成功标记相互独立：一方失败不该阻止另一方入库。"""
    outcome = await _run(_routes(rank=_fixture("bestselling_no_products.html")))

    assert outcome.rank_ok is False
    assert outcome.catalog_complete is True
    assert len(outcome.products) == 4


async def test_fetch_store_meta_failure_is_not_fatal():
    """meta 抖动不该让整次拉取失败——站点加入时已验过是 Shopify。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/meta.json":
            return httpx.Response(500, text="oops")
        return _routes()(request)

    outcome = await _run(handler)

    assert outcome.meta is None
    assert outcome.status == FETCH_OK
    assert outcome.catalog_complete is True


async def test_fetch_store_skips_rank_when_catalog_is_empty():
    """名次只在目录非空时才拉——这个不变量正是「0 个商品 = 解析器坏了」的依据。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/meta.json":
            return httpx.Response(200, text=_meta_with_count(0))
        return httpx.Response(200, text=_fixture("products_empty_page.json"))

    outcome = await _run(handler)

    assert outcome.products == ()
    assert "/collections/all" not in calls
    assert outcome.rank_ok is False


async def test_fetch_store_rank_stops_at_top_n():
    outcome = await _run(_routes(), rank_top_n=3)

    assert len(outcome.rank_handles) == 3
