"""客户端断开即取消分析查询（#154，openspec/changes/analysis-query-efficiency，design D2）。

辅助函数与接口接线是纯 asyncio / ASGI，单元测试即可；「数据库侧真的停了」要真库才能证明，
那部分只在 TEST_DATABASE_URL 时运行（与其他集成测试同一个 gate，CI 上会真跑）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid

import pytest
from sqlalchemy import text
from starlette.requests import Request

from app.common.disconnect import CLIENT_CLOSED_REQUEST, cancel_on_disconnect
from config import settings

TEST_URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_URL, reason="设置 TEST_DATABASE_URL 运行断开取消的真库测试")


def _request(receive) -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": []}, receive)


async def _never_disconnects():
    await asyncio.Event().wait()


def _disconnect_after(event: asyncio.Event, *, leading_empty: bool = False):
    """``event`` 置位后返回断开；``leading_empty`` 时先返回一条空的 http.request。"""
    sent_empty = False

    async def receive():
        nonlocal sent_empty
        if leading_empty and not sent_empty:
            sent_empty = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await event.wait()
        return {"type": "http.disconnect"}

    return receive


async def test_returns_result_when_work_finishes_first():
    async def work():
        return "ok"

    assert await cancel_on_disconnect(_request(_never_disconnects), work()) == "ok"


async def test_disconnect_cancels_work_and_returns_499():
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def work():
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    t0 = time.monotonic()
    response = await cancel_on_disconnect(_request(_disconnect_after(started, leading_empty=True)), work())
    assert response.status_code == CLIENT_CLOSED_REQUEST
    # 返回前查询任务的 finally 已经跑完：并发位、截止时刻、连接都在返回前收拾好
    assert cancelled.is_set()
    assert time.monotonic() - t0 < 1


async def test_work_exception_propagates():
    async def work():
        raise RuntimeError("查询失败")

    with pytest.raises(RuntimeError, match="查询失败"):
        await cancel_on_disconnect(_request(_never_disconnects), work())


async def test_outer_cancellation_cancels_work_too():
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def work():
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    outer = asyncio.create_task(cancel_on_disconnect(_request(_never_disconnects), work()))
    await started.wait()
    outer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await outer
    assert cancelled.is_set()


# ---------------------------------------------------------------- 接口接线（真实 ASGI 调用）


class _StubUser:
    is_super = True
    permission_codes: set[str] = set()
    id = 1


class _StubAsyncSession:
    async def close(self) -> None:
        pass


def _build_app(monkeypatch, search):
    from fastapi import FastAPI

    from app.auth.simple_auth import simple_auth
    from app.common.error_handlers import register_error_handlers
    from app.common.request_logging import RequestLoggingMiddleware
    from app.table.analysis import analysis_api as api_mod
    from app.table.analysis import analysis_service as service
    from database import get_async_db

    monkeypatch.setattr(service, "search", search)
    monkeypatch.setattr(service, "_search_gate", None)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(api_mod.analysis_router, prefix="/api/analysis")
    app.dependency_overrides[get_async_db] = _StubAsyncSession
    app.dependency_overrides[simple_auth.get_current_user] = lambda: _StubUser()
    return RequestLoggingMiddleware(app)


async def _call(app, disconnect: asyncio.Event):
    body = json.dumps({"site": "US", "view": "DAY"}).encode()
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": "/api/analysis/search", "raw_path": b"/api/analysis/search",
        "query_string": b"", "root_path": "", "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 80),
    }
    body_sent = False

    async def receive():
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    sent = []

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


async def test_search_endpoint_cancels_query_on_disconnect(monkeypatch, caplog):
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def slow_search(*_args, **_kwargs):
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    app = _build_app(monkeypatch, slow_search)
    with caplog.at_level(logging.INFO, logger="app.common.request_logging"):
        status = await _call(app, started)
    assert status == CLIENT_CLOSED_REQUEST
    assert cancelled.is_set()
    # 请求摘要不把断开伪造成正常完成
    summary = [r.message for r in caplog.records if "请求摘要" in r.message]
    assert len(summary) == 1 and "outcome=client_disconnected" in summary[0]


async def test_search_endpoint_runs_to_completion_when_switch_is_off(monkeypatch):
    from app.table.search.search_schemas import AnalysisSearchResponse

    monkeypatch.setattr(settings, "ANALYSIS_CANCEL_ON_DISCONNECT_ENABLED", False)
    started = asyncio.Event()

    async def search(*_args, **_kwargs):
        started.set()
        await asyncio.sleep(0.1)
        return AnalysisSearchResponse(success=True, data=[], total=0, site="US", view="DAY", date=None)

    app = _build_app(monkeypatch, search)
    assert await _call(app, started) == 200


# ---------------------------------------------------------------- 真库：数据库侧真的停了


@pytest.fixture()
async def fresh_async_engine():
    """全局异步引擎、空池：池里的连接绑定建立它们的事件循环，用前用后各清一次（同预算测试）。"""
    from database import async_engine

    await async_engine.dispose(close=False)
    try:
        yield async_engine
    finally:
        await async_engine.dispose()


async def _active(engine, marker: str) -> int:
    async with engine.connect() as conn:
        return (await conn.execute(text(
            "SELECT count(*) FROM pg_stat_activity WHERE state = 'active' AND query LIKE :m"
            " AND pid <> pg_backend_pid()"
        ), {"m": f"%{marker}%"})).scalar()


@needs_db
async def test_async_engine_checks_client_connection(fresh_async_engine):
    async with fresh_async_engine.connect() as conn:
        value = (await conn.execute(text(
            "SELECT setting FROM pg_settings WHERE name = 'client_connection_check_interval'"
        ))).scalar()
    assert value == str(settings.INTERACTIVE_CLIENT_CHECK_INTERVAL_MS)


@needs_db
async def test_disconnect_stops_the_statement_in_the_database(fresh_async_engine):
    """断开后数据库在检测间隔内停止该语句；同池并发的另一条语句不受影响，池可继续用。

    会话建在被取消的任务之外、由调用方关闭——与请求会话由依赖创建和收尾的形态一致。
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    marker, other_marker = f"kiq-dc-{uuid.uuid4().hex[:8]}", f"kiq-other-{uuid.uuid4().hex[:8]}"
    started = asyncio.Event()
    # 先单独建一条连接：SQLAlchemy 引擎首次建连的初始化带互斥，两个协程并发首次建连会卡死在那把锁上
    # （应用里 main.py 的 lifespan 在开始服务前就用异步引擎建过连接；测试里 dispose 后会重来）。
    assert await _active(fresh_async_engine, marker) == 0

    async def other():
        async with AsyncSession(fresh_async_engine) as db:
            return (await db.execute(text(f"SELECT 'done' FROM pg_sleep(1.5) /* {other_marker} */"))).scalar()

    other_task = asyncio.create_task(other())
    db = AsyncSession(fresh_async_engine)
    try:
        async def work():
            await db.execute(text("SELECT 1"))  # 先取到连接，确保下一条语句一发出就在服务端执行
            started.set()
            await db.execute(text(f"SELECT pg_sleep(5) /* {marker} */"))

        async def disconnect_soon():
            await started.wait()
            await asyncio.sleep(0.3)
            return {"type": "http.disconnect"}

        response = await cancel_on_disconnect(_request(disconnect_soon), work())
        assert response.status_code == CLIENT_CLOSED_REQUEST
    finally:
        await db.close()

    # 轮询的是数据库里的状态，没有可等待的事件；一个检测间隔再加 1.5 s 余量，远短于 pg_sleep(5)
    polls = int((settings.INTERACTIVE_CLIENT_CHECK_INTERVAL_MS / 1000 + 1.5) / 0.1)
    for _ in range(polls):
        if not await _active(fresh_async_engine, marker):
            break
        await asyncio.sleep(0.1)
    assert await _active(fresh_async_engine, marker) == 0
    assert await other_task == "done"
    async with AsyncSession(fresh_async_engine) as db2:
        assert (await db2.execute(text("SELECT 1"))).scalar() == 1
