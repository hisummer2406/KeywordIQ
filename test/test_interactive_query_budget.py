"""交互查询的执行预算与分析查询并发闸（#148，openspec/changes/post-import-query-readiness）。

并发闸是纯 asyncio，单元测试即可；执行预算要真库才能证明「数据库确实取消了语句、连接可复用」，
那部分只在 TEST_DATABASE_URL 时运行（与其他集成测试同一个 gate，CI 上会真跑）。
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from config import settings

TEST_URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_URL, reason="设置 TEST_DATABASE_URL 运行执行预算的真库测试")


@pytest.fixture()
def gate(monkeypatch):
    """并发上限 1、排队上限 50ms 的闸，每个用例从干净状态开始。"""
    from app.table.analysis import analysis_service as svc

    monkeypatch.setattr(settings, "ANALYSIS_MAX_CONCURRENT_SEARCHES", 1)
    monkeypatch.setattr(settings, "ANALYSIS_SEARCH_QUEUE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(svc, "_search_gate", None)
    return svc


async def _hold(svc, entered: asyncio.Event, leave: asyncio.Event):
    async with svc.search_slot():
        entered.set()
        await leave.wait()


async def test_search_slot_rejects_when_full_then_recovers(gate):
    entered, leave = asyncio.Event(), asyncio.Event()
    holder = asyncio.create_task(_hold(gate, entered, leave))
    await entered.wait()

    with pytest.raises(gate.AnalysisBusyError) as info:
        async with gate.search_slot():
            pytest.fail("闸已满时不该进入")
    assert info.value.http_status == 503
    assert info.value.code == "analysis.busy"

    leave.set()
    await holder
    async with gate.search_slot():
        pass
    # 超时放弃的那次没有吞掉许可：闸回到满额
    assert gate._search_semaphore()._value == 1


async def test_waiter_gets_slot_if_released_within_queue_timeout(gate, monkeypatch):
    monkeypatch.setattr(settings, "ANALYSIS_SEARCH_QUEUE_TIMEOUT_SECONDS", 1.0)
    entered, leave = asyncio.Event(), asyncio.Event()
    holder = asyncio.create_task(_hold(gate, entered, leave))
    await entered.wait()

    asyncio.get_running_loop().call_later(0.05, leave.set)
    async with gate.search_slot():
        pass
    await holder
    assert gate._search_semaphore()._value == 1


async def test_slot_is_released_when_the_search_raises(gate):
    with pytest.raises(RuntimeError):
        async with gate.search_slot():
            raise RuntimeError("查询失败")
    assert gate._search_semaphore()._value == 1


@pytest.fixture()
async def fresh_async_engine():
    """全局异步引擎，但池是空的。

    池里的连接绑定在建立它们的事件循环上：前面用 TestClient 的用例（跑 app lifespan）会在
    另一个 loop 里往池里放连接，那个 loop 关掉后再取出来就是坏的（``test_database`` 排在
    它们后面时同样会挂，见 #148 PR 说明）。用前用后各清一次池，只测引擎配置本身。
    """
    from database import async_engine

    await async_engine.dispose(close=False)
    try:
        yield async_engine
    finally:
        await async_engine.dispose()


@needs_db
async def test_async_engine_carries_interactive_budget_but_sync_engine_does_not(fresh_async_engine):
    """预算只挂在异步引擎（交互读路径）上；导入 / 导出 / 迁移走的同步引擎不受约束。"""
    from database import engine

    probe = text("SELECT setting FROM pg_settings WHERE name = 'statement_timeout'")
    async with fresh_async_engine.connect() as conn:
        assert (await conn.execute(probe)).scalar() == str(settings.INTERACTIVE_STATEMENT_TIMEOUT_MS)
    with engine.connect() as conn:
        assert conn.execute(probe).scalar() == "0"


@needs_db
async def test_statement_over_budget_is_canceled_and_connection_reused(fresh_async_engine):
    """数据库到点取消语句（不是客户端放弃等待），连接随后可继续用，服务端不残留那条查询。"""
    from app.common.error_handlers import _is_query_canceled

    async with fresh_async_engine.connect() as conn:
        await conn.execute(text("SET statement_timeout = 100"))
        started = time.monotonic()
        with pytest.raises(DBAPIError) as info:
            await conn.execute(text("SELECT pg_sleep(5)"))
        assert time.monotonic() - started < 2
        assert _is_query_canceled(info.value)

        await conn.rollback()
        assert (await conn.execute(text("SELECT 1"))).scalar() == 1
        lingering = (await conn.execute(text(
            "SELECT count(*) FROM pg_stat_activity "
            " WHERE state = 'active' AND query LIKE 'SELECT pg_sleep(5)%'"
        ))).scalar()
        assert lingering == 0
