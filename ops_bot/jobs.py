"""把 `checks/*` 与 `alerts.evaluate` 接成 `scheduler.ScheduledJob` 列表。

各 `checks/*` 模块只做"判断是不是异常"，不知道飞书、不知道去重/静默——那些是
`alerts.evaluate` 与本模块的职责。这里是唯一知道"整套巡检长什么样"的地方，
新增一类巡检从这里的 `build_jobs()` 接入。
"""

from __future__ import annotations

import logging

from config import settings
from ops_bot import alerts, backup, crud, daily_summary
from ops_bot.checks import alembic_drift, heartbeat, import_status, logs, resources, service
from ops_bot.database import OpsAsyncSessionFactory
from ops_bot.feishu.client import FeishuAPIError, FeishuClient
from ops_bot.scheduler import ScheduledJob, daily_at

logger = logging.getLogger(__name__)

_QUICK_SECONDS = 60
_FULL_SECONDS = 300
_FIFTEEN_MIN_SECONDS = 900

_POOL_WATER_WARN_RATIO = 0.9
_BACKUP_OVERDUE_WARN_HOURS = 24
_BACKUP_OVERDUE_CRIT_HOURS = 48

_LOG_PATH = "logs/app.log"

# ops_task/ops_audit_log/ops_dedup_key 的保留期（design.md D3「任务/审计保留」）。
# 数据量远小于业务事实表，不需要分区，daily 分组里直接 DELETE。
_TASK_RETENTION_DAYS = 90


def build_jobs(feishu_client: FeishuClient, chat_id: str) -> list[ScheduledJob]:
    async def service_check() -> str:
        healthy = await service.is_app_healthy()
        async with OpsAsyncSessionFactory() as session:
            await alerts.evaluate(
                session, feishu_client, chat_id,
                alert_key="service-down", level="crit", is_alerting=not healthy,
                message="业务服务健康检查未通过（/health 非 200）",
                silence_minutes=settings.OPS_ALERT_SILENCE_MINUTES,
            )
            await session.commit()
        return "healthy" if healthy else "unhealthy"

    async def pool_check() -> str:
        ratio = await service.pool_water_level()
        is_alerting = ratio is not None and ratio >= _POOL_WATER_WARN_RATIO
        async with OpsAsyncSessionFactory() as session:
            await alerts.evaluate(
                session, feishu_client, chat_id,
                alert_key="pool-water-high", level="warn", is_alerting=is_alerting,
                message=f"连接池水位过高：{ratio:.0%}" if ratio is not None else "连接池水位探测失败",
                silence_minutes=settings.OPS_ALERT_SILENCE_MINUTES,
            )
            await session.commit()
        return "unknown" if ratio is None else f"{ratio:.0%}"

    async def import_stuck_check() -> str:
        async with OpsAsyncSessionFactory() as session:
            stuck = await import_status.find_stuck_batches(
                session,
                day_timeout_minutes=settings.OPS_IMPORT_DAY_TIMEOUT_MINUTES,
                week_timeout_minutes=settings.OPS_IMPORT_WEEK_TIMEOUT_MINUTES,
            )
            stuck_keys = {f"import-stuck-{b['id']}" for b in stuck}
            for batch in stuck:
                await alerts.evaluate(
                    session, feishu_client, chat_id,
                    alert_key=f"import-stuck-{batch['id']}", level="crit", is_alerting=True,
                    message=(
                        f"导入批次 #{batch['id']}（{batch['site']} {batch['period_type']} "
                        f"{batch['report_date']}）长时间未完成，当前状态 {batch['status']}"
                    ),
                    silence_minutes=settings.OPS_ALERT_SILENCE_MINUTES,
                )
            # 已不再卡死（批次已终结或被清理）的旧告警键需要单独 resolve——
            # 它们不会出现在本轮 stuck 列表里，无法通过上面的循环带出 is_alerting=False。
            previously_unresolved = await crud.list_unresolved_alerts(session)
            for state in previously_unresolved:
                if state.alert_key.startswith("import-stuck-") and state.alert_key not in stuck_keys:
                    await alerts.evaluate(
                        session, feishu_client, chat_id,
                        alert_key=state.alert_key, level="crit", is_alerting=False,
                        message=state.alert_key,
                        silence_minutes=settings.OPS_ALERT_SILENCE_MINUTES,
                    )
            await session.commit()
        return f"{len(stuck)} 个卡死批次"

    async def import_notify() -> str:
        async with OpsAsyncSessionFactory() as session:
            last = await crud.last_succeeded_at(session, "IMPORT_NOTIFY")
            new_batches = await import_status.find_new_terminal_batches(session, since=last)
            failed_sends = 0
            for batch in new_batches:
                if batch["status"] == "COMPLETED":
                    await backup_safe_record_baseline(session, batch)
                    text = (
                        f"✅ 导入完成：{batch['site']} {batch['period_type']} "
                        f"{batch['report_date']}，耗时 {batch['processing_seconds']}s"
                    )
                else:
                    text = (
                        f"❌ 导入失败：{batch['site']} {batch['period_type']} "
                        f"{batch['report_date']}，原因：{batch['error_message']}"
                    )
                # 通知失败与执行失败分开记录：单条发送失败不能让整轮检查判定失败，
                # 更不能让 for 循环中途抛出——那会导致本轮已经算好的基线
                # （backup_safe_record_baseline）因为下面这次 commit 从没执行到而
                # 连带丢失，是比"少发一条通知"严重得多的后果。
                try:
                    await feishu_client.send_text(chat_id, text)
                except FeishuAPIError:
                    logger.exception("导入完成/失败通知发送失败：%s", text)
                    failed_sends += 1
            await session.commit()
        summary = f"{len(new_batches)} 条新完成/失败通知"
        if failed_sends:
            summary += f"（其中 {failed_sends} 条发送失败，详见应用日志）"
        return summary

    async def backup_safe_record_baseline(session, batch: dict) -> None:
        # processing_seconds 可能因老数据缺失（列默认 0，语义上"未记录"与"0 秒完成"
        # 无法区分）；宁可漏记一条基线观测，也不要把一个假的 0 秒样本混进基线表
        # 污染人工核对——基线只是观测参考，缺一条不影响任何判定。
        if batch.get("processing_seconds"):
            await import_status.record_baseline(session, batch)

    async def log_error_rate_check() -> str:
        count = logs.count_recent_errors(_LOG_PATH, since_minutes=5)
        is_alerting = count >= settings.OPS_ERROR_RATE_THRESHOLD
        async with OpsAsyncSessionFactory() as session:
            await alerts.evaluate(
                session, feishu_client, chat_id,
                alert_key="log-error-rate", level="warn", is_alerting=is_alerting,
                message=f"近 5 分钟错误/5xx 计数 {count} 条，超过阈值 {settings.OPS_ERROR_RATE_THRESHOLD}",
                silence_minutes=settings.OPS_ALERT_SILENCE_MINUTES,
            )
            await session.commit()
        return f"{count} 条"

    async def backup_overdue_check() -> str:
        age_hours = await backup.latest_backup_age_hours()
        is_alerting = age_hours is None or age_hours >= _BACKUP_OVERDUE_WARN_HOURS
        level = "crit" if age_hours is not None and age_hours >= _BACKUP_OVERDUE_CRIT_HOURS else "warn"
        message = (
            "从未成功备份过" if age_hours is None
            else f"距上次成功备份已 {age_hours:.1f} 小时"
        )
        async with OpsAsyncSessionFactory() as session:
            await alerts.evaluate(
                session, feishu_client, chat_id,
                alert_key="backup-overdue", level=level, is_alerting=is_alerting,
                message=message, silence_minutes=settings.OPS_ALERT_SILENCE_MINUTES,
            )
            await session.commit()
        return message

    async def disk_check() -> str:
        percent = resources.disk_usage_percent()
        level = "crit" if percent >= resources.DISK_CRIT_PERCENT else "warn"
        is_alerting = percent >= resources.DISK_WARN_PERCENT
        async with OpsAsyncSessionFactory() as session:
            await alerts.evaluate(
                session, feishu_client, chat_id,
                alert_key="disk-usage", level=level, is_alerting=is_alerting,
                message=f"磁盘使用率 {percent:.1f}%",
                silence_minutes=settings.OPS_ALERT_SILENCE_MINUTES,
            )
            await session.commit()
        return f"{percent:.1f}%"

    async def backup_job() -> str:
        return await backup.run_backup(retention_days=settings.OPS_BACKUP_RETENTION_DAYS)

    async def alembic_drift_check() -> str:
        in_sync = alembic_drift.is_in_sync()
        async with OpsAsyncSessionFactory() as session:
            await alerts.evaluate(
                session, feishu_client, chat_id,
                alert_key="alembic-drift", level="crit", is_alerting=in_sync is False,
                message="生产库 alembic 版本与代码不一致（current != heads）",
                silence_minutes=settings.OPS_ALERT_SILENCE_MINUTES,
            )
            await session.commit()
        if in_sync is None:
            return "探测失败"
        return "同步" if in_sync else "漂移"

    async def daily_summary_job() -> str:
        return await daily_summary.send(feishu_client, chat_id)

    async def prune_task_records_job() -> str:
        async with OpsAsyncSessionFactory() as session:
            deleted = await crud.prune_old_task_records(session, retention_days=_TASK_RETENTION_DAYS)
            await session.commit()
        return f"清理了 {deleted} 条超过 {_TASK_RETENTION_DAYS} 天的任务记录"

    return [
        ScheduledJob(kind=heartbeat.KIND, interval_seconds=_QUICK_SECONDS, run=heartbeat.run),
        ScheduledJob(kind="SERVICE_CHECK", interval_seconds=_FULL_SECONDS, run=service_check),
        ScheduledJob(kind="POOL_CHECK", interval_seconds=_FULL_SECONDS, run=pool_check),
        ScheduledJob(kind="IMPORT_STUCK_CHECK", interval_seconds=_FULL_SECONDS, run=import_stuck_check),
        ScheduledJob(kind="IMPORT_NOTIFY", interval_seconds=_FULL_SECONDS, run=import_notify),
        ScheduledJob(kind="LOG_ERROR_RATE_CHECK", interval_seconds=_FULL_SECONDS, run=log_error_rate_check),
        ScheduledJob(kind="BACKUP_OVERDUE_CHECK", interval_seconds=_FULL_SECONDS, run=backup_overdue_check),
        ScheduledJob(kind="DISK_CHECK", interval_seconds=_FIFTEEN_MIN_SECONDS, run=disk_check),
        ScheduledJob(kind="BACKUP", interval_seconds=86400, run=backup_job, should_run=daily_at(3, 0)),
        ScheduledJob(
            kind="ALEMBIC_DRIFT_CHECK", interval_seconds=86400, run=alembic_drift_check,
            should_run=daily_at(3, 30),
        ),
        ScheduledJob(
            kind="DAILY_SUMMARY", interval_seconds=86400, run=daily_summary_job,
            should_run=daily_at(9, 0),
        ),
        ScheduledJob(
            kind="PRUNE_TASK_RECORDS", interval_seconds=86400, run=prune_task_records_job,
            should_run=daily_at(4, 0),
        ),
    ]
