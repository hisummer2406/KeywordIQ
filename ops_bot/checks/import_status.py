"""导入批次巡检：卡死检测、完成/失败通知、耗时基线记录。

只读 `analysis.import_batch`（`ops_bot_role` 的权限边界，见迁移
`d4e5f6a7b8c9`），**不 import 业务 `app.table.upload.import_batch_model`**——
`ops_bot/__init__.py` 已经定了这条独立性边界，这里改用裸 SQL 读取需要的列，
不依赖业务 ORM 模型的 Python 类，两边各自演进互不牵连（业务模型改列名不会让
这个模块 import 失败，只会在真的用到那一列时才报错，且报错发生在查询执行而非
import 阶段）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

STATUS_ACTIVE = ("PROCESSING", "UPLOADING")
STATUS_TERMINAL = ("COMPLETED", "FAILED")


async def find_stuck_batches(
    session: AsyncSession, *, day_timeout_minutes: int, week_timeout_minutes: int
) -> list[dict]:
    """`PROCESSING`/`UPLOADING` 且已运行超过对应期次类型阈值的批次。

    用 `created_at` 而非"距上次检测有无变化"——`import_batch` 没有 `updated_at`
    列，`created_at` 到现在的时长本就是"这批导入已经跑了多久"的直接度量，"长时间
    无进展"在这里等价于"跑得比该期次类型的正常耗时长太多还没完成"。
    """
    result = await session.execute(
        text(
            "SELECT id, site, period_type, report_date, status, created_at "
            "FROM analysis.import_batch WHERE status = ANY(:statuses)"
        ),
        {"statuses": list(STATUS_ACTIVE)},
    )
    now = datetime.now(UTC)
    stuck = []
    for row in result.mappings():
        threshold_minutes = day_timeout_minutes if row["period_type"] == "DAY" else week_timeout_minutes
        age_minutes = (now - row["created_at"]).total_seconds() / 60
        if age_minutes >= threshold_minutes:
            stuck.append(dict(row))
    return stuck


async def any_active(session: AsyncSession) -> bool:
    """是否存在 `PROCESSING`/`UPLOADING` 的批次——重启/发布前置检查用
    （design.md D5「导入运行期间阻止重启与发布」，比 `find_stuck_batches` 更轻量：
    只要存在就够了，不需要按阈值判断"卡没卡死"）。
    """
    result = await session.execute(
        text("SELECT 1 FROM analysis.import_batch WHERE status = ANY(:statuses) LIMIT 1"),
        {"statuses": list(STATUS_ACTIVE)},
    )
    return result.first() is not None


async def find_new_terminal_batches(
    session: AsyncSession, *, since: datetime | None, until: datetime
) -> list[dict]:
    """`(since, until]` 区间内新完成（`COMPLETED`/`FAILED`）的批次，`since=None` 时
    返回空列表（调用方首次运行没有基准点，不该把历史上所有批次当作"新"一次性通知一遍）。

    `until` 必须是调用方发起本次查询**之前**捕获的时刻（下一轮的水位线正是这个值）。
    没有这道上界时，本次查询会把 `until` 捕获之后、查询实际执行之前才完成的批次也
    一并扫入——那批次这一轮通知过，水位线推进到 `until` 之后，下一轮 `since=until`
    仍然 `completed_at > since` 为真，会被再通知一遍（PR #99 复审第四轮 P1）。
    """
    if since is None:
        return []
    result = await session.execute(
        text(
            "SELECT id, site, period_type, report_date, status, error_message, "
            "processing_seconds, completed_at "
            "FROM analysis.import_batch "
            "WHERE status = ANY(:statuses) AND completed_at > :since AND completed_at <= :until "
            "ORDER BY completed_at"
        ),
        {"statuses": list(STATUS_TERMINAL), "since": since, "until": until},
    )
    return [dict(row) for row in result.mappings()]


async def record_baseline(session: AsyncSession, batch: dict) -> None:
    """成功批次的耗时观测值，只记录不驱动自动阈值学习（design.md D3）。

    `ON CONFLICT (batch_id) DO NOTHING`：部分批次通知失败时水位线不推进
    （见 `jobs.import_notify`），下一轮会把包括已成功记录过基线的批次在内的整个
    区间重新扫一遍——同一 `batch_id` 落两次是这条重试语义下的正常情形，不是异常，
    应静默丢弃第二次而不是抛唯一约束错误中断整轮通知（PR #99 复审第四轮 P1）。
    """
    await session.execute(
        text(
            "INSERT INTO ops.ops_import_baseline (period_type, batch_id, duration_seconds) "
            "VALUES (:period_type, :batch_id, :duration_seconds) "
            "ON CONFLICT (batch_id) DO NOTHING"
        ),
        {
            "period_type": batch["period_type"],
            "batch_id": batch["id"],
            "duration_seconds": batch["processing_seconds"],
        },
    )
