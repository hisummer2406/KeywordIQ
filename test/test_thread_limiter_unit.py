"""线程池上限的单元测试（不连库）。

anyio 的默认线程限流器是**三类工作共用**的一个全局闸门：

- 同步 `def` 端点（导出）与健康检查探活——每条线程占一条同步数据库连接
- bcrypt 哈希/校验（每次登录 ~247 ms 纯 CPU）——**不占数据库连接**
- 同步 BackgroundTask（`run_import`）——一条线程占住整场导入，可达数十分钟

所以它不能从同步连接池的大小推导。曾经写成 `DB_POOL_SIZE + DB_MAX_OVERFLOW`，
而 async-query-path 的任务 7.3 计划把同步池降到 2+3——那会让限流器悄悄变成 5，
一个导入在跑就只剩 4 条线程给全部登录，登录延迟会随并发直接劣化。
"""
import anyio.to_thread
import pytest

import main
from config import Settings


def test_thread_pool_limit_is_its_own_setting():
    """它是独立配置项，不从连接池取值。"""
    assert "THREAD_POOL_LIMIT" in Settings.model_fields


def test_thread_pool_limit_is_not_derived_from_sync_pool():
    """把同步池降到设计目标（2+3）时，线程池上限不受影响。"""
    downsized = Settings(DB_POOL_SIZE=2, DB_MAX_OVERFLOW=3)

    assert downsized.THREAD_POOL_LIMIT > 5


@pytest.mark.asyncio
async def test_configure_thread_limiter_applies_the_setting():
    limiter = anyio.to_thread.current_default_thread_limiter()
    original = limiter.total_tokens
    try:
        applied = main.configure_thread_limiter()

        assert applied == main.settings.THREAD_POOL_LIMIT
        assert limiter.total_tokens == main.settings.THREAD_POOL_LIMIT
    finally:
        limiter.total_tokens = original
