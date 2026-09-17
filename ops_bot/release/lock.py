"""运维动作锁：执行重启/发布前抢占，与
`app/table/upload/import_service._OPS_ACTION_LOCK_KEY` 是同一把咨询锁（同一个整数
key，两边独立定义常量值，因为 ops_bot 不 import 业务 `app.*` 代码）。

抢不到就说明导入正抢先持有它，执行器据此中止，不强行抢占、不重试
（design.md D5「导入协调锁机制」）。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from sqlalchemy import text

from ops_bot.database import ops_async_engine

OPS_ACTION_LOCK_KEY = 0x4B4F  # 必须与 import_service._OPS_ACTION_LOCK_KEY 同值


@asynccontextmanager
async def try_acquire():
    """`async with try_acquire() as got:` —— `got` 为 `False` 时调用方不应继续执行。

    用**专属连接**（`ops_async_engine.connect()`，不是 `OpsAsyncSessionFactory`）持有锁
    直到退出上下文——这段时间要跨越"执行 docker/deploy.sh 子进程"这种分钟级操作，
    因此不能像 `import_service._ops_action_in_progress` 那样"抢完立刻放"，必须真正
    持有到动作结束。

    **不能用 `AsyncSession`：`session.commit()` 会把连接还给连接池。** 咨询锁是
    连接（会话）级的，一旦这条连接被放回池子，池里的其他活动可能复用它并在同一条
    连接上重新"抢到"这把已经被自己持有的锁（同一连接上的会话级锁可重入），使这把
    锁形同虚设；真正 `unlock` 时也可能分到另一条连接，对错误的后端解锁而静默失败，
    让锁永久挂在原连接上直到它被关闭。这与 `app/table/upload/import_service.py`
    的 `import_lock` 是同一类缺陷，那里的文档字符串有实测复现记录
    （PR #99 复审第三轮 P1）。`ops_async_engine.connect()` 返回的是独立签出的连接，
    不会因为中途的 `commit()` 被交还。
    """
    async with ops_async_engine.connect() as conn:
        got = bool(
            (await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": OPS_ACTION_LOCK_KEY}))
            .scalar()
        )
        await conn.commit()
        try:
            yield got
        finally:
            if got:
                await conn.execute(
                    text("SELECT pg_advisory_unlock(:k)"), {"k": OPS_ACTION_LOCK_KEY}
                )
                await conn.commit()
