"""ops schema 的数据访问层。纯数据库操作，不含业务判断（同构项目 `*_crud.py` 分层约定）。"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def try_claim_dedup_key(session: AsyncSession, event_id: str) -> bool:
    """尝试认领一个事件 ID。返回 True 表示这是第一次见到（已插入，调用方应继续处理），
    返回 False 表示之前已经处理过（`INSERT ... ON CONFLICT DO NOTHING` 未插入新行）。

    用 `ON CONFLICT DO NOTHING` 而非"先 SELECT 再 INSERT"：两条语句之间存在竞态窗口，
    飞书的快速重试可能在窗口内到达，`ON CONFLICT` 把判断收进单条原子语句。
    """
    result = await session.execute(
        text(
            "INSERT INTO ops.ops_dedup_key (event_id) VALUES (:event_id) "
            "ON CONFLICT (event_id) DO NOTHING"
        ),
        {"event_id": event_id},
    )
    return result.rowcount > 0
