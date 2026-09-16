"""ops-bot crud 层集成测试：验证 ops schema 下真实的幂等 INSERT ... ON CONFLICT。

默认跳过，只在设置 TEST_DATABASE_URL 时运行（沿用 V2 集成测试的 gate 模式）。
`ops_bot_role` 是 alembic 迁移创建的集群级角色（跨数据库共享），本文件用
`OPS_DB_ROLE_PASSWORD` 环境变量拼一条指向同一个测试库的连接串——它不是
`config.Settings` 字段（见迁移文件说明，只有迁移本身直接读 `os.environ`），
这里同样直接读 `os.environ`，不经 `config.settings`。

    TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        OPS_DB_ROLE_PASSWORD=change-me-in-production \\
        uv run pytest test/test_ops_bot_crud_integration.py -v
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)


def _ops_role_async_url() -> str | None:
    """把 TEST_DATABASE_URL 的账号换成 ops_bot_role，指向同一个测试库。

    角色是迁移建的集群级对象，前提是这个测试库已经 `alembic upgrade head` 到
    包含本变更的版本——迁移未跑到位时下面的连接会失败，测试按"库未迁移"的既有
    惯例 skip，不当作用例失败。
    """
    from sqlalchemy.engine import make_url

    password = os.environ.get("OPS_DB_ROLE_PASSWORD", "change-me-in-production")
    url = make_url(TEST_URL).set(drivername="postgresql+asyncpg")
    return str(url.set(username="ops_bot_role", password=password))


@pytest.fixture()
async def ops_session():
    engine = create_async_engine(_ops_role_async_url())
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        await engine.dispose()
        pytest.skip("测试库尚未迁移到 ops schema（先运行 alembic upgrade head）")

    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_first_claim_returns_true_and_inserts(ops_session):
    from ops_bot.crud import try_claim_dedup_key

    event_id = f"evt-{uuid.uuid4()}"

    claimed = await try_claim_dedup_key(ops_session, event_id)
    await ops_session.commit()

    assert claimed is True


async def test_replayed_event_id_returns_false(ops_session):
    """飞书重放同一个事件 ID：第二次不应被当作新事件处理。"""
    from ops_bot.crud import try_claim_dedup_key

    event_id = f"evt-{uuid.uuid4()}"

    first = await try_claim_dedup_key(ops_session, event_id)
    await ops_session.commit()
    second = await try_claim_dedup_key(ops_session, event_id)
    await ops_session.commit()

    assert first is True
    assert second is False
