"""慢查询事件监听覆盖异步引擎（database.py 决策 D 第一层）的集成测试。

此前 before/after_cursor_execute 只挂在同步引擎（engine）上，/api/analysis/search
等面向请求的查询全部走异步引擎（async_engine），对这条路径完全没有慢查询记录——
这是需要真实数据库连接才能验证的行为（cursor 级事件在真实执行一条 SQL 时才触发），
故是集成测试而非单元测试，默认跳过，只在设置 TEST_DATABASE_URL 时运行。

每个测试自建一个一次性异步引擎（NullPool），不复用 database.py 的模块级
``async_engine``——沿用 test_site_scoped_query_integration.py 已验证过的模式：
pytest-asyncio 默认每个测试一个新事件循环，跨测试复用同一个连接池会让后一个测试
在不同的事件循环上操作前一个测试留下的连接，asyncpg 报 "attached to a different
loop"。这里验证的是 ``database._register_slow_query_listener`` 这个共享实现，
不是 database.py 里那个具体的模块级单例，NullPool 不影响这一点。

    TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run pytest test/test_slow_query_integration.py -v
"""
from __future__ import annotations

import logging
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from config import settings
from database import _register_slow_query_listener

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


async def test_async_engine_query_exceeding_threshold_is_logged(caplog, monkeypatch):
    monkeypatch.setattr(settings, "SLOW_QUERY_THRESHOLD_SECONDS", 0.0)
    test_engine = create_async_engine(_async_url(TEST_URL), poolclass=NullPool)
    _register_slow_query_listener(test_engine.sync_engine)

    try:
        with caplog.at_level(logging.WARNING, logger="database"):
            async with test_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
    finally:
        await test_engine.dispose()

    warnings = [r for r in caplog.records if "慢查询检测" in r.message]
    assert warnings, "异步引擎执行的查询应当触发慢查询记录（此前只有同步引擎会）"


async def test_async_engine_query_under_threshold_is_not_logged(caplog, monkeypatch):
    monkeypatch.setattr(settings, "SLOW_QUERY_THRESHOLD_SECONDS", 60.0)
    test_engine = create_async_engine(_async_url(TEST_URL), poolclass=NullPool)
    _register_slow_query_listener(test_engine.sync_engine)

    try:
        with caplog.at_level(logging.WARNING, logger="database"):
            async with test_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
    finally:
        await test_engine.dispose()

    warnings = [r for r in caplog.records if "慢查询检测" in r.message]
    assert not warnings
