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
    # **不能用 `str(url)`**：SQLAlchemy 的 URL.__str__ 默认把密码掩码成字面量
    # "***" 防止意外记进日志——用在这里会构造出一条真的拿 "***" 当密码去连接的
    # 连接串，永远认证失败，而失败恰好落进下面 fixture 的"未迁移，跳过"分支，
    # 表现成一个查不出问题的假阴性（实测踩过：即便角色密码确认无误，这里仍然
    # 每次都判定"库未迁移"）。要拿到真实密码必须显式 `render_as_string(hide_password=False)`。
    return url.set(username="ops_bot_role", password=password).render_as_string(hide_password=False)


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


async def test_try_cancel_task_succeeds_when_pending_confirm(ops_session):
    from ops_bot.crud import try_cancel_task
    from ops_bot.model import TASK_PENDING_CONFIRM, OpsTask

    task_id = str(uuid.uuid4())
    ops_session.add(OpsTask(id=task_id, kind="RESTART_APP", status=TASK_PENDING_CONFIRM))
    await ops_session.commit()

    cancelled = await try_cancel_task(ops_session, task_id, output="用户取消")
    await ops_session.commit()

    assert cancelled is True
    task = await ops_session.get(OpsTask, task_id)
    assert task.status == "FAILED"
    assert task.output == "用户取消"


async def test_try_cancel_task_loses_race_after_confirm(ops_session):
    """确认已经抢先落地（状态已是 CONFIRMED）时，取消必须是空操作，不能覆盖状态
    （PR #99 复审第三轮 P1）。
    """
    from ops_bot.crud import try_cancel_task
    from ops_bot.model import TASK_CONFIRMED, OpsTask

    task_id = str(uuid.uuid4())
    ops_session.add(OpsTask(id=task_id, kind="DEPLOY", status=TASK_CONFIRMED))
    await ops_session.commit()

    cancelled = await try_cancel_task(ops_session, task_id, output="用户取消")
    await ops_session.commit()

    assert cancelled is False
    task = await ops_session.get(OpsTask, task_id)
    assert task.status == TASK_CONFIRMED


async def test_reconcile_orphaned_tasks_marks_running_and_confirmed_failed(ops_session):
    """启动回收只处理 RUNNING/CONFIRMED，PENDING_CONFIRM 不受影响
    （PR #99 复审第三轮 P2）。
    """
    from ops_bot.crud import reconcile_orphaned_tasks
    from ops_bot.model import TASK_CONFIRMED, TASK_PENDING_CONFIRM, TASK_RUNNING, OpsTask

    running_id, confirmed_id, pending_id = (str(uuid.uuid4()) for _ in range(3))
    ops_session.add_all(
        [
            OpsTask(id=running_id, kind="BACKUP", status=TASK_RUNNING),
            OpsTask(id=confirmed_id, kind="DEPLOY", status=TASK_CONFIRMED),
            OpsTask(id=pending_id, kind="RESTART_APP", status=TASK_PENDING_CONFIRM),
        ]
    )
    await ops_session.commit()

    # 不断言绝对 count：`ops_task` 表被同一次 pytest 运行里的其他用例共用，不做
    # 每用例清理（同构本文件其余用例），断言只认自己刚插入的三行的最终状态。
    await reconcile_orphaned_tasks(ops_session, output="ops-bot 重启，执行结果未知，请人工核实")
    await ops_session.commit()

    running = await ops_session.get(OpsTask, running_id)
    confirmed = await ops_session.get(OpsTask, confirmed_id)
    pending = await ops_session.get(OpsTask, pending_id)
    assert running.status == "FAILED"
    assert confirmed.status == "FAILED"
    assert pending.status == TASK_PENDING_CONFIRM


async def test_retire_alert_keys_resolves_only_unresolved_listed_keys(ops_session):
    """只关闭清单内且仍未解决的记录：清单外的告警不受影响，已解决的不被改写，
    重复调用是幂等的（返回 0）。"""
    from ops_bot.crud import retire_alert_keys
    from ops_bot.model import OpsAlertState

    suffix = uuid.uuid4().hex[:8]
    retired_key, resolved_key, other_key = (f"{p}-{suffix}" for p in ("retired", "resolved", "other"))
    await ops_session.execute(
        text(
            "INSERT INTO ops.ops_alert_state (alert_key, level, last_fired_at, consecutive_count, resolved_at) "
            "VALUES (:r, 'warn', now(), 5, NULL), (:d, 'warn', now(), 0, now() - interval '1 day'), "
            "(:o, 'crit', now(), 3, NULL)"
        ),
        {"r": retired_key, "d": resolved_key, "o": other_key},
    )
    await ops_session.commit()
    before_resolved = (await ops_session.get(OpsAlertState, resolved_key)).resolved_at

    count = await retire_alert_keys(ops_session, [retired_key, resolved_key])
    await ops_session.commit()
    ops_session.expire_all()

    assert count == 1
    retired = await ops_session.get(OpsAlertState, retired_key)
    assert retired.resolved_at is not None
    assert retired.consecutive_count == 0
    assert (await ops_session.get(OpsAlertState, resolved_key)).resolved_at == before_resolved
    other = await ops_session.get(OpsAlertState, other_key)
    assert other.resolved_at is None
    assert other.consecutive_count == 3

    assert await retire_alert_keys(ops_session, [retired_key, resolved_key]) == 0
    assert await retire_alert_keys(ops_session, []) == 0
