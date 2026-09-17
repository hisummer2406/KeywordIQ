"""ops-bot 与导入侧的双向咨询锁协调集成测试（design.md D5「导入协调锁机制」）。

默认跳过，只在设置 TEST_DATABASE_URL 时运行。验证的是同一个咨询锁 key
（`ops_bot.release.lock.OPS_ACTION_LOCK_KEY` 与
`app.table.upload.import_service._OPS_ACTION_LOCK_KEY`，两边独立定义、必须同值）
在业务库连接与 ops_bot_role 连接之间确实共享——PostgreSQL 的咨询锁按数据库而非
按角色隔离，只要连的是同一个数据库，两个角色互相看得见对方持有的锁。

`ops_bot.release.lock.try_acquire()` 内部用的是真实 `OPS_DATABASE_URL_ASYNC`
（通常指向开发库，不是这次测试用的 `TEST_DATABASE_URL`）——这里必须把它换成指向
`TEST_URL` 的 `ops_bot_role` 连接，否则两把"锁"根本落在两个不同的数据库上，
测试会在不知不觉中变成"什么都没验证"。

    TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run pytest test/test_ops_bot_lock_coordination_integration.py -v
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Session

from app.table.upload.import_service import _OPS_ACTION_LOCK_KEY, _ops_action_in_progress
from ops_bot.release.lock import OPS_ACTION_LOCK_KEY, try_acquire

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)


def _ops_role_async_url() -> str | None:
    from sqlalchemy.engine import make_url

    password = os.environ.get("OPS_DB_ROLE_PASSWORD", "change-me-in-production")
    url = make_url(TEST_URL).set(drivername="postgresql+asyncpg")
    # **不能用 `str(url)`**：SQLAlchemy 的 URL.__str__ 默认把密码掩码成字面量
    # "***"——用在这里会构造出一条真的拿 "***" 当密码去连接的连接串，认证必然
    # 失败，而失败恰好落进下面 fixture 的"未迁移，跳过"分支，表现成一个查不出
    # 问题的假阴性（实测踩过：角色密码确认无误，这里仍然每次判定"库未迁移"）。
    return url.set(username="ops_bot_role", password=password).render_as_string(hide_password=False)


@pytest.fixture()
async def ops_engine():
    """指向 `TEST_URL` 的 `ops_bot_role` 引擎，替身顶替 `try_acquire()` 内部真实用的
    `OPS_DATABASE_URL_ASYNC`——迁移未跑到位（角色/schema 不存在）时按既有惯例优雅
    跳过。

    **是引擎不是会话工厂**：`try_acquire()` 用 `ops_async_engine.connect()` 签出
    专属连接持锁到动作结束，不能用 `AsyncSession`（`session.commit()` 会把连接还给
    池子，导致锁在池子里被静默转移或解锁到错误的连接上，PR #99 复审第三轮 P1）。
    """
    engine = create_async_engine(_ops_role_async_url())
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        await engine.dispose()
        pytest.skip("测试库尚未迁移到 ops schema（先运行 alembic upgrade head）")

    yield engine
    await engine.dispose()


@pytest.fixture()
def business_session():
    engine = create_engine(TEST_URL)
    session = Session(bind=engine)
    yield session
    session.close()
    engine.dispose()


async def test_ops_bot_lock_visible_to_import_side(ops_engine, business_session):
    """ops-bot 持有运维动作锁时，导入侧的探测函数必须看到"进行中"。"""
    with patch("ops_bot.release.lock.ops_async_engine", ops_engine):
        async with try_acquire() as got:
            assert got is True

            in_progress = _ops_action_in_progress(business_session)

    assert in_progress is True


async def test_lock_released_after_context_exit(ops_engine, business_session):
    """退出 `try_acquire()` 上下文后锁必须真正释放，不留孤儿锁。"""
    with patch("ops_bot.release.lock.ops_async_engine", ops_engine):
        async with try_acquire() as got:
            assert got is True

    in_progress = _ops_action_in_progress(business_session)
    assert in_progress is False


def test_lock_keys_are_actually_the_same_value():
    """健全性检查：两边用的确实是同一个 key 值，不是两个恰好都叫这个名字的常量。"""
    assert OPS_ACTION_LOCK_KEY == _OPS_ACTION_LOCK_KEY == 0x4B4F
