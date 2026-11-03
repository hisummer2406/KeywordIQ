"""独立站单次拉取的硬性截止与租约清除（change serialize-dtc-fetch D2、D3）。

不连库：把认领、拉取、落库替换成记录调用的替身，只验证 ``refresh_due_store`` 的编排——
超时必须落成「无数据的失败」、异常路径必须尽力清租约。截止短于租约这条不变量由模块级
断言守住，这里另钉一次常量关系。
"""

from __future__ import annotations

import asyncio

import pytest

from app.table.competitor import store_crud
from app.table.competitor import store_service as service
from app.table.competitor.store_fetcher import FETCH_FAILED


@pytest.fixture()
def calls(monkeypatch):
    recorded: dict[str, list] = {"persist": [], "release": []}
    # refresh_due_store 会真的建一个 httpx.AsyncClient（拉取本身被替身替换）；本机若设了
    # SOCKS 代理变量，httpx 建客户端即报缺 socksio——与被测逻辑无关，清掉。
    for var in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(var, raising=False)

    monkeypatch.setattr(service, "_claim_due_store", lambda hours, now: "slow.example")
    monkeypatch.setattr(
        service,
        "_persist",
        lambda domain, outcome, now: recorded["persist"].append(outcome) or _summary(outcome),
    )
    monkeypatch.setattr(service, "_release_lease", lambda domain: recorded["release"].append(domain))
    return recorded


def _summary(outcome):
    return service.RefreshSummary(
        domain="slow.example",
        status=outcome.status,
        new_products=0,
        price_changes=0,
        ranks_written=0,
        delisted=0,
        relisted=0,
        catalog_complete=outcome.catalog_complete,
        rank_ok=outcome.rank_ok,
    )


def test_截止短于租约():
    assert service._FETCH_DEADLINE_SECONDS < store_crud.FETCH_LEASE.total_seconds()


async def test_超过截止落成无数据的失败(monkeypatch, calls):
    async def hang(*args, **kwargs):
        await asyncio.sleep(3600)

    monkeypatch.setattr(service, "fetch_store", hang)
    monkeypatch.setattr(service, "_FETCH_DEADLINE_SECONDS", 0.05)

    summary = await service.refresh_due_store()

    assert summary.status == FETCH_FAILED
    (outcome,) = calls["persist"]
    assert outcome.products == ()
    assert outcome.rank_handles == ()
    assert outcome.catalog_complete is False
    assert "拉取超时" in outcome.error


async def test_落库出错时尽力清租约并继续抛出(monkeypatch, calls):
    async def ok(*args, **kwargs):
        return service._timed_out_outcome()

    def boom(domain, outcome, now):
        raise RuntimeError("db down")

    monkeypatch.setattr(service, "fetch_store", ok)
    monkeypatch.setattr(service, "_persist", boom)

    with pytest.raises(RuntimeError):
        await service.refresh_due_store()
    assert calls["release"] == ["slow.example"]

    # 轮询 job 吞掉异常、本轮记为没干活，不连累同轮其他 job
    assert await service.refresh_due_store_round() is False
