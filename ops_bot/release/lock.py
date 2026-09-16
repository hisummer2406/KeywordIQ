"""运维动作锁：执行重启/发布前抢占，与
`app/table/upload/import_service._OPS_ACTION_LOCK_KEY` 是同一把咨询锁（同一个整数
key，两边独立定义常量值，因为 ops_bot 不 import 业务 `app.*` 代码）。

抢不到就说明导入正抢先持有它，执行器据此中止，不强行抢占、不重试
（design.md D5「导入协调锁机制」）。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from sqlalchemy import text

from ops_bot.database import OpsAsyncSessionFactory

OPS_ACTION_LOCK_KEY = 0x4B4F  # 必须与 import_service._OPS_ACTION_LOCK_KEY 同值


@asynccontextmanager
async def try_acquire():
    """`async with try_acquire() as got:` —— `got` 为 `False` 时调用方不应继续执行。

    用独立连接（`OpsAsyncSessionFactory` 开的新 session 天然是独立连接）持有锁直到
    退出上下文——这段时间要跨越"执行 docker/deploy.sh 子进程"这种分钟级操作，
    因此不能像 `import_service._ops_action_in_progress` 那样"抢完立刻放"，必须真正
    持有到动作结束。
    """
    async with OpsAsyncSessionFactory() as session:
        got = bool(
            (await session.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": OPS_ACTION_LOCK_KEY}))
            .scalar()
        )
        await session.commit()
        try:
            yield got
        finally:
            if got:
                await session.execute(
                    text("SELECT pg_advisory_unlock(:k)"), {"k": OPS_ACTION_LOCK_KEY}
                )
                await session.commit()
