"""ops schema 的数据访问层。纯数据库操作，不含业务判断（同构项目 `*_crud.py` 分层约定）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ops_bot.model import (
    TASK_CONFIRMED,
    TASK_EXPIRED,
    TASK_FAILED,
    TASK_PENDING_CONFIRM,
    TASK_RUNNING,
    TASK_SUCCEEDED,
    OpsAlertState,
    OpsAuditLog,
    OpsBackupRecord,
    OpsTask,
)


async def create_task(
    session: AsyncSession, *, kind: str, payload: dict | None = None, requested_by_open_id: str | None = None
) -> str:
    """新建一条 RUNNING 任务记录，返回 task_id。"""
    task_id = str(uuid.uuid4())
    session.add(
        OpsTask(
            id=task_id,
            kind=kind,
            status=TASK_RUNNING,
            payload=payload or {},
            requested_by_open_id=requested_by_open_id,
        )
    )
    await session.flush()
    return task_id


async def mark_task_succeeded(session: AsyncSession, task_id: str, output: str = "") -> None:
    await session.execute(
        update(OpsTask)
        .where(OpsTask.id == task_id)
        .values(status=TASK_SUCCEEDED, output=output, finished_at=func.now())
    )


async def mark_task_failed(session: AsyncSession, task_id: str, output: str = "") -> None:
    await session.execute(
        update(OpsTask)
        .where(OpsTask.id == task_id)
        .values(status=TASK_FAILED, output=output, finished_at=func.now())
    )


async def create_pending_confirm_task(
    session: AsyncSession,
    *,
    kind: str,
    payload: dict,
    requested_by_open_id: str | None,
    ttl_minutes: int,
) -> tuple[str, str]:
    """新建一条待确认任务，返回 `(task_id, confirm_token)`——重启/发布专用
    （design.md D5）。`confirm_token` 与 `task_id` 一起编进卡片按钮的 value，
    回调时两者都要匹配，防止已失效卡片的按钮被重放。
    """
    task_id = str(uuid.uuid4())
    confirm_token = str(uuid.uuid4())
    session.add(
        OpsTask(
            id=task_id,
            kind=kind,
            status=TASK_PENDING_CONFIRM,
            payload=payload,
            confirm_token=confirm_token,
            confirm_expires_at=datetime.now(UTC) + timedelta(minutes=ttl_minutes),
            requested_by_open_id=requested_by_open_id,
        )
    )
    await session.flush()
    return task_id, confirm_token


async def get_task(session: AsyncSession, task_id: str) -> OpsTask | None:
    return await session.get(OpsTask, task_id)


async def try_confirm_task(session: AsyncSession, task_id: str) -> bool:
    """乐观锁：只有仍处于 `PENDING_CONFIRM` 时才能转 `CONFIRMED`。返回 `True`
    表示这次调用真正完成了转移——并发重复点击（双击、飞书重放）只有一次会拿到
    `True`，其余直接判"已处理"，不会执行两次（design.md D5）。
    """
    result = await session.execute(
        update(OpsTask)
        .where(OpsTask.id == task_id, OpsTask.status == TASK_PENDING_CONFIRM)
        .values(status=TASK_CONFIRMED)
    )
    return result.rowcount > 0


async def try_cancel_task(session: AsyncSession, task_id: str, output: str = "") -> bool:
    """乐观锁：只有仍处于 `PENDING_CONFIRM` 时才能转 `FAILED`（用户取消），与
    :func:`try_confirm_task` 同一条纪律。

    **不能用无条件的 `mark_task_failed`**：确认与取消两个按钮的回调各自在独立的
    session 里先读一次任务状态再决定怎么处理，中间没有互斥——若确认的回调先一步
    把状态改成 `CONFIRMED`（且已经把执行 closure 交给调用方在后台跑），取消的回调
    仍可能读到旧的 `PENDING_CONFIRM` 快照并对同一行发出无条件 UPDATE，把一个
    实际正在执行重启/发布的任务覆盖标记成"用户取消"，与后台真正在跑的执行结果
    互相踩踏（PR #99 复审第三轮 P1）。加上 `WHERE status = PENDING_CONFIRM` 之后，
    确认已经抢先落地的情况下这次 UPDATE 影响 0 行，调用方据此判"已处理"。
    """
    result = await session.execute(
        update(OpsTask)
        .where(OpsTask.id == task_id, OpsTask.status == TASK_PENDING_CONFIRM)
        .values(status=TASK_FAILED, output=output, finished_at=func.now())
    )
    return result.rowcount > 0


async def mark_task_expired(session: AsyncSession, task_id: str) -> None:
    await session.execute(
        update(OpsTask)
        .where(OpsTask.id == task_id, OpsTask.status == TASK_PENDING_CONFIRM)
        .values(status=TASK_EXPIRED, finished_at=func.now())
    )


async def mark_task_running(session: AsyncSession, task_id: str) -> None:
    await session.execute(update(OpsTask).where(OpsTask.id == task_id).values(status=TASK_RUNNING))


async def reconcile_orphaned_tasks(session: AsyncSession, output: str) -> int:
    """服务启动时把上一次运行遗留的 `RUNNING`/`CONFIRMED` 任务标记为失败，返回处理条数。

    进程被杀（宿主机重启、systemd 重启）时，正在执行的备份/重启/发布任务的后台
    协程随进程一起消失，但 `ops_task` 行仍停在 `RUNNING`（或已确认但还没来得及转
    `RUNNING` 的 `CONFIRMED`）——不会自然完结，`任务` 指令会把它永远显示成"进行中"，
    而它早已没有任何协程在跑（PR #99 复审第三轮 P2）。必须在接受新任务前显式标记为
    一个不会被误读成"仍在进行"的终态。

    单进程部署（`ops-bot` 是一个 systemd 服务，不是集群）下这个操作是安全的：
    任一时刻只可能有一个进程在跑，启动时看到的 `RUNNING`/`CONFIRMED` 行必然属于
    上一次已经不存在的进程实例，不会误伤当前正在执行的任务。
    """
    result = await session.execute(
        update(OpsTask)
        .where(OpsTask.status.in_((TASK_RUNNING, TASK_CONFIRMED)))
        .values(status=TASK_FAILED, output=output, finished_at=func.now())
    )
    return result.rowcount


async def last_succeeded_at(session: AsyncSession, kind: str) -> datetime | None:
    """某类任务最近一次成功完成的时间；从未成功过则 `None`。

    调度循环靠它判断"距上次成功执行的时间"，而不是进程内存里的下次触发时间戳
    ——见 `ops_bot/scheduler.py` 与 design.md D1。
    """
    result = await session.execute(
        select(OpsTask.finished_at)
        .where(OpsTask.kind == kind, OpsTask.status == TASK_SUCCEEDED)
        .order_by(OpsTask.finished_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


_NOTIFY_WATERMARK_LINE_PREFIX = "watermark="


def encode_import_notify_output(summary: str, watermark: datetime | None) -> str:
    """把 `import_notify` 本轮的扫描水位线编进任务 `output`，供
    :func:`last_import_notify_watermark` 读回。见该函数文档字符串。
    """
    marker = watermark.isoformat() if watermark is not None else ""
    return f"{summary}\n{_NOTIFY_WATERMARK_LINE_PREFIX}{marker}"


async def last_import_notify_watermark(session: AsyncSession, kind: str) -> datetime | None:
    """`import_notify` 上一次成功运行时记下的扫描水位线——编码在该任务的 `output`
    里，**不是** `created_at`/`finished_at`。

    这两者都曾被当过水位线，都不对：`finished_at`（查询与发送通知都跑完之后才
    落库）会漏记"本轮查询已执行、本轮任务尚未标记完成"这段窗口内转为终态的批次
    （PR #99 复审第二轮 P2）。改用 `created_at`（`scheduler._run_once` 在
    `job.run()` 真正发出查询**之前**就已落库）能堵上那个窗口，但它本身也不是
    "本轮实际扫描到哪个完成时刻"的准确代理：`create_task` 落库与 `job.run()`
    内部真正发出查询之间还有一段间隙，凡是在这段间隙里完成的批次，其
    `completed_at` 会同时大于本轮与上一轮的 `created_at`——本轮命中一次（大于
    上一轮 `created_at`），下一轮又命中一次（大于本轮 `created_at`），被通知
    两遍（PR #99 复审第三轮 P2，"新鲜证据"部分）。

    水位线因此必须是查询发出前捕获的真实时间戳，且只在本轮全部通知都发送成功时
    才推进——任一条发送失败就维持在上一轮的水位线不变：宁可下一轮对已经发送
    成功的其余批次重复通知一次，也不让发送失败的那一条永久漏记（同一 issue，
    与 `jobs.import_notify` 的实现互相印证）。
    """
    result = await session.execute(
        select(OpsTask.output)
        .where(OpsTask.kind == kind, OpsTask.status == TASK_SUCCEEDED)
        .order_by(OpsTask.finished_at.desc())
        .limit(1)
    )
    output = result.scalar_one_or_none()
    if not output:
        return None
    for line in output.splitlines():
        if line.startswith(_NOTIFY_WATERMARK_LINE_PREFIX):
            value = line[len(_NOTIFY_WATERMARK_LINE_PREFIX) :]
            return datetime.fromisoformat(value) if value else None
    return None


async def list_unresolved_alerts(session: AsyncSession) -> list[OpsAlertState]:
    result = await session.execute(
        select(OpsAlertState)
        .where(OpsAlertState.resolved_at.is_(None), OpsAlertState.last_fired_at.is_not(None))
        .order_by(OpsAlertState.last_fired_at.desc())
    )
    return list(result.scalars())


async def get_alert_state(session: AsyncSession, alert_key: str) -> OpsAlertState | None:
    result = await session.execute(select(OpsAlertState).where(OpsAlertState.alert_key == alert_key))
    return result.scalar_one_or_none()


async def upsert_alert_fired(session: AsyncSession, alert_key: str, level: str, consecutive_count: int) -> None:
    """记录一次"仍处于异常"的判定：`last_fired_at`/`level`/`consecutive_count` 写成
    当前值，`resolved_at` 清空。`ops_bot/alerts.py` 决定是否要因此真的发一条通知——
    这层只负责状态落库，不含"要不要发"的判断（分层约定：crud 不含业务判断）。
    """
    await session.execute(
        text(
            "INSERT INTO ops.ops_alert_state (alert_key, level, last_fired_at, consecutive_count, resolved_at) "
            "VALUES (:key, :level, now(), :count, NULL) "
            "ON CONFLICT (alert_key) DO UPDATE SET "
            "level = :level, last_fired_at = now(), consecutive_count = :count, resolved_at = NULL"
        ),
        {"key": alert_key, "level": level, "count": consecutive_count},
    )


async def bump_alert_consecutive_count(session: AsyncSession, alert_key: str, consecutive_count: int) -> None:
    """只推进"连续异常计数"，不动 `last_fired_at`——静默期内仍在异常的轮次要计数，
    但不发新通知，`last_fired_at` 必须保持"上一次真正发送通知的时刻"这个含义不变，
    否则静默期判定会被每一轮都重置、永远不会到期（`ops_bot/alerts.py`）。
    """
    await session.execute(
        text("UPDATE ops.ops_alert_state SET consecutive_count = :count WHERE alert_key = :key"),
        {"key": alert_key, "count": consecutive_count},
    )


async def mark_alert_resolved(session: AsyncSession, alert_key: str) -> None:
    await session.execute(
        text(
            "UPDATE ops.ops_alert_state SET resolved_at = now(), consecutive_count = 0 "
            "WHERE alert_key = :key"
        ),
        {"key": alert_key},
    )


async def set_alert_maintenance_until(session: AsyncSession, alert_key: str, until: datetime) -> None:
    """打开/延长一个告警键的维护窗口（design.md D3「维护窗口」）。

    用 `ON CONFLICT` 而不要求该键必须先存在：重启/发布可能在这个告警键从未触发过
    （从未异常过）时就开始执行，此时也要能提前立好维护窗口，等巡检真的检测到异常时
    直接读到它，不必区分"这个键有没有历史记录"。
    """
    await session.execute(
        text(
            "INSERT INTO ops.ops_alert_state (alert_key, maintenance_until) VALUES (:key, :until) "
            "ON CONFLICT (alert_key) DO UPDATE SET maintenance_until = :until"
        ),
        {"key": alert_key, "until": until},
    )


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


async def record_backup(
    session: AsyncSession,
    *,
    status: str,
    object_key: str | None,
    size_bytes: int | None,
    failed_stage: str | None,
) -> None:
    session.add(
        OpsBackupRecord(
            status=status,
            object_key=object_key,
            size_bytes=size_bytes,
            failed_stage=failed_stage,
            finished_at=func.now(),
        )
    )


async def latest_succeeded_backup(session: AsyncSession) -> OpsBackupRecord | None:
    result = await session.execute(
        select(OpsBackupRecord)
        .where(OpsBackupRecord.status == "SUCCEEDED")
        .order_by(OpsBackupRecord.finished_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def recent_tasks(session: AsyncSession, limit: int = 10) -> list[OpsTask]:
    """最近的运维任务记录，按创建时间倒序——供飞书"任务查询"指令使用。

    `limit` 由调用方给死值（指令处理层不接受用户传参调大它），这是"有界查询"
    约束落在这一层的体现：不因为查询接口存在就意味着可以无界。
    """
    result = await session.execute(select(OpsTask).order_by(OpsTask.created_at.desc()).limit(limit))
    return list(result.scalars())


async def write_audit_log(
    session: AsyncSession,
    *,
    task_id: str | None,
    actor_open_id: str | None,
    action: str,
    target: str,
    target_sha: str | None,
    confirmed_at: datetime | None,
    result: str,
    failed_stage: str | None,
) -> None:
    """受限动作（重启/发布/备份）的审计追加记录。表结构本身没有自由文本"输出"字段
    ——凭证/密钥这类敏感内容原本就没有地方可落，不需要在这一层单独脱敏
    （design.md 「审计记录」：凭证及敏感日志不发群、不写入审计明文，落在这张表上
    天然满足，风险面在 `ops_task.output` 那个自由文本字段，见该表的既有截断处理）。
    """
    session.add(
        OpsAuditLog(
            task_id=task_id,
            actor_open_id=actor_open_id or "",
            action=action,
            target=target,
            target_sha=target_sha,
            confirmed_at=confirmed_at,
            result=result,
            failed_stage=failed_stage,
        )
    )


async def prune_old_task_records(session: AsyncSession, *, retention_days: int = 90) -> int:
    """清理超过保留期的 `ops_task`/`ops_audit_log`/`ops_dedup_key`（design.md D3
    「任务/审计保留」）。返回删除的 `ops_task` 行数，供 daily 分组的清理任务写进
    输出摘要。

    `ops_dedup_key`/`ops_audit_log` 不设外键级联，各自独立按自己的时间列清理——
    `ops_audit_log` 保留審计的完整性不依赖对应的 `ops_task` 是否还在，两者是
    "同时清理"不是"级联清理"。
    """
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    task_result = await session.execute(text("DELETE FROM ops.ops_task WHERE created_at < :cutoff"), {"cutoff": cutoff})
    await session.execute(text("DELETE FROM ops.ops_audit_log WHERE created_at < :cutoff"), {"cutoff": cutoff})
    await session.execute(text("DELETE FROM ops.ops_dedup_key WHERE created_at < :cutoff"), {"cutoff": cutoff})
    return task_result.rowcount
