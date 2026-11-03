"""独立站拉取遇到 429 限流的处理（change fix-dtc-catalog-collection D1）。

生产 2026-11-03：89 站只有 26 站拿到完整目录，22 站因第 1 页 429 被判成「未开放商品
接口」。这里钉住：429 按 ``Retry-After`` 等待重试、总等待有预算、耗尽记抓取失败而不是
接口未开放、畅销榜中途限流不写截断名次。等待通过替换 ``_throttle_sleep`` 记录，不真睡。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.common.errors import BadRequestError
from app.table.competitor import store_fetcher
from app.table.competitor import store_service as service
from app.table.competitor.store_fetcher import (
    FETCH_FAILED,
    FETCH_NO_CATALOG_API,
    FETCH_OK,
    fetch_store,
)

FIXTURES = Path(__file__).parent / "fixtures" / "dtc"
DOMAIN = "example.com"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _meta() -> str:
    payload = json.loads(_fixture("meta.json"))
    payload.pop("published_products_count", None)
    return json.dumps(payload)


@pytest.fixture()
def waits(monkeypatch) -> list[float]:
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(store_fetcher, "_throttle_sleep", fake_sleep)
    return recorded


def _throttled(retry_after: str | None = "60") -> httpx.Response:
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    return httpx.Response(429, headers=headers, text="Too Many Requests")


def _handler(*, catalog_429: dict[int, int] | None = None, rank_429: dict[int, int] | None = None,
             retry_after: str | None = "60", catalog_pages: int = 2, rank_pages: int = 1):
    """``catalog_429[page] = n``：该页前 n 次请求返回 429，之后正常。"""
    catalog_429 = dict(catalog_429 or {})
    rank_429 = dict(rank_429 or {})

    def handler(request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, request.url.params
        page = int(params.get("page", "1"))
        if path == "/meta.json":
            return httpx.Response(200, text=_meta())
        if path == "/products.json":
            if catalog_429.get(page, 0) > 0:
                catalog_429[page] -= 1
                return _throttled(retry_after)
            if page <= catalog_pages:
                return httpx.Response(200, text=_fixture("products_page1.json"))
            return httpx.Response(200, text=_fixture("products_empty_page.json"))
        if path == "/collections/all":
            if rank_429.get(page, 0) > 0:
                rank_429[page] -= 1
                return _throttled(retry_after)
            if page <= rank_pages:
                return httpx.Response(200, text=_fixture("bestselling_page1.html"))
            return httpx.Response(200, text=_fixture("bestselling_no_products.html"))
        return httpx.Response(404)

    return handler


async def _run(handler, **kwargs):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await fetch_store(
            client,
            DOMAIN,
            max_pages=40,
            rank_top_n=kwargs.pop("rank_top_n", 100),
            delay_ms=0,
            retry_backoff_seconds=0,
            **kwargs,
        )


async def test_一次限流后按RetryAfter等待并拉完整(waits):
    outcome = await _run(_handler(catalog_429={1: 1}, catalog_pages=1))

    assert waits == [60.0]
    assert outcome.status == FETCH_OK
    assert outcome.catalog_complete is True


async def test_中途页限流后恢复仍可完整(waits):
    outcome = await _run(_handler(catalog_429={2: 2}, catalog_pages=2))

    assert waits == [60.0, 60.0]
    assert outcome.catalog_complete is True


async def test_第1页持续限流记抓取失败而不是接口未开放(waits):
    """原先 429 当作「确定的 4xx」：第 1 页即判未开放接口，界面说「重试也不会好」。"""
    outcome = await _run(_handler(catalog_429={1: 99}))

    assert outcome.status == FETCH_FAILED
    assert outcome.products_json_ok is True
    assert outcome.catalog_complete is False
    assert "限流" in (outcome.error or "")
    assert sum(waits) <= store_fetcher._THROTTLE_BUDGET_SECONDS


async def test_中途页持续限流不完整且已拿到的照常返回(waits):
    outcome = await _run(_handler(catalog_429={2: 99}, catalog_pages=2))

    assert outcome.status == FETCH_FAILED
    assert outcome.catalog_complete is False
    assert len(outcome.products) > 0  # 第 1 页的商品照常交给写入层


async def test_RetryAfter缺失或非法按默认值_超大按上限(waits):
    await _run(_handler(catalog_429={1: 1}, retry_after=None, catalog_pages=1))
    await _run(_handler(catalog_429={1: 1}, retry_after="Wed, 21 Oct 2026 07:28:00 GMT",
                        catalog_pages=1))
    await _run(_handler(catalog_429={1: 1}, retry_after="3600", catalog_pages=1))

    assert waits == [
        store_fetcher._THROTTLE_DEFAULT_WAIT_SECONDS,
        store_fetcher._THROTTLE_DEFAULT_WAIT_SECONDS,
        store_fetcher._THROTTLE_MAX_WAIT_SECONDS,
    ]


async def test_等待预算由整次拉取共享(waits):
    """meta、目录各页、畅销榜各页共用一份预算：最坏情况占住轮询的时长必须可预期。"""
    outcome = await _run(
        _handler(catalog_429={1: 2, 2: 2}, catalog_pages=2), throttle_budget_seconds=180
    )

    assert waits == [60.0, 60.0, 60.0]
    assert outcome.status == FETCH_FAILED


async def test_畅销榜中途限流耗尽不写截断名次(waits):
    outcome = await _run(
        _handler(rank_429={2: 99}, catalog_pages=1, rank_pages=2), rank_top_n=100
    )

    assert outcome.catalog_complete is True
    assert outcome.rank_ok is False
    assert outcome.rank_handles == ()


async def test_404仍判接口未开放且不等待(waits):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/meta.json":
            return httpx.Response(200, text=_meta())
        return httpx.Response(404, text="nope")

    outcome = await _run(handler)

    assert outcome.status == FETCH_NO_CATALOG_API
    assert waits == []


async def test_加入站点探测遇限流给出限流原因而不是不是Shopify(monkeypatch, waits):
    """加入是请求路径，不等 Retry-After；但不能把被限流的站点说成「不是 Shopify」。"""
    monkeypatch.setattr(
        service,
        "_store_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: _throttled())),
    )

    with pytest.raises(BadRequestError) as exc:
        await service._probe_shopify(DOMAIN)
    assert exc.value.message == service.STORE_THROTTLED
    assert waits == []

    results = await service._probe_many([DOMAIN])
    assert results[DOMAIN] == service.STORE_THROTTLED


async def test_RetryAfter为0也按下限等待_预算照样封顶(waits):
    """Retry-After: 0 时若真的不等，每次都不扣预算，重试会对着站点无限连发。"""
    outcome = await _run(_handler(catalog_429={1: 10_000}, retry_after="0"))

    assert outcome.status == FETCH_FAILED
    assert waits and min(waits) == store_fetcher._THROTTLE_MIN_WAIT_SECONDS
    assert len(waits) == int(
        store_fetcher._THROTTLE_BUDGET_SECONDS // store_fetcher._THROTTLE_MIN_WAIT_SECONDS
    )
