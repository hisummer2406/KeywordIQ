"""把 `checks/*` 与 `alerts.evaluate` 接成 `scheduler.ScheduledJob` 列表。

各 `checks/*` 模块只做"判断是不是异常"，不知道飞书、不知道去重/静默——那些是
`alerts.evaluate` 与本模块的职责。这里是唯一知道"整套巡检长什么样"的地方，
新增一类巡检从这里的 `build_jobs()` 接入。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from config import settings
from ops_bot import alerts, crud, daily_summary, retention
from ops_bot.checks import alembic_drift, heartbeat, import_status, logs, resources, service
from ops_bot.database import OpsAsyncSessionFactory
from ops_bot.feishu.client import FeishuAPIError, FeishuClient
from ops_bot.scheduler import ScheduledJob, daily_at

logger = logging.getLogger(__name__)

_QUICK_SECONDS = 60
_FULL_SECONDS = 300
_FIFTEEN_MIN_SECONDS = 900

_POOL_WATER_WARN_RATIO = 0.9

_LOG_PATH = "logs/app.log"

# 已从 `build_jobs` 移除的巡检所用的告警键。移除一项巡检不会自动关掉它遗留的未解决
# 记录（没有人再调用 `alerts.evaluate`），启动时由 `main` 静默置为已解决，见
# `crud.retire_alert_keys`。**移除巡检时把它的告警键加进来**。
RETIRED_ALERT_KEYS = ("backup-overdue",)

# ops_task/ops_audit_log/ops_dedup_key 的保留期（design.md D3「任务/审计保留」）。
# 数据量远小于业务事实表，不需要分区，daily 分组里直接 DELETE。
_TASK_RETENTION_DAYS = 90

_RETENTION_ALERT_DETAIL_LIMIT = 20


def _summarize_retention(label: str, result: dict) -> str:
    if result["status"] == "lock_skipped":
        return f"{label}: 导入进行中，已跳过"
    dropped = sum(len(names) for names in result.get("dropped", {}).values())
    purged = sum(result.get("purged_stats", {}).values())
    months = ",".join(result.get("blocked_months", [])) or "无"
    return f"{label}: 分区 {dropped} 个，残留统计 {purged} 行，拦截月份 {months}"


def _retention_blocked_message(result: dict) -> str:
    gaps = result.get("blocked", [])
    if not gaps:
        return "保留清理归档校验"
    shown = gaps[:_RETENTION_ALERT_DETAIL_LIMIT]
    lines = [
        f"{g['site']} {g['period_type']} {g['report_date']}: {g['reason']}" for g in shown
    ]
    more = f"\n另有 {len(gaps) - len(shown)} 条未列出" if len(gaps) > len(shown) else ""
    months = ",".join(result.get("blocked_months", []))
    return f"保留清理被归档校验拦截，月份 {months} 的数据已保留：\n" + "\n".join(lines) + more


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
        # `ratio is None` 是探测失败（见 pool_water_level 的文档约定），不是"水位为
        # 0"——当健康、不能被判为 False 静默放过；否则 /health/pool 不可达、格式
        # 错误这类未知状态会让 alerts.evaluate 对一个已经在告警的水位误报"已恢复"，
        # 或对一次新的探测失败干脆不报（PR #99 Codex 复审 P2）。
        is_alerting = ratio is None or ratio >= _POOL_WATER_WARN_RATIO
        async with OpsAsyncSessionFactory() as session:
            await alerts.evaluate(
                session, feishu_client, chat_id,
                alert_key="pool-water-high", level="warn", is_alerting=is_alerting,
                message=(
                    f"连接池水位过高：{ratio:.0%}"
                    if ratio is not None
                    else "连接池水位探测失败（未知状态，按异常处理）"
                ),
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
            last = await crud.last_import_notify_watermark(session, "IMPORT_NOTIFY")
            # 查询发出前捕获，而不是借用调度器落库的任务 created_at——两者之间那段
            # 间隙里完成的批次会被本轮与下一轮各命中一次、通知两遍
            # （crud.last_import_notify_watermark 的文档字符串，PR #99 复审第三轮 P2）。
            cutoff = datetime.now(UTC)
            new_batches = await import_status.find_new_terminal_batches(session, since=last, until=cutoff)
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
        # 只有全部发送成功才把水位线推进到本轮 cutoff；有失败就原样保留上一轮的
        # 水位线，下一轮会连同已经发送成功的批次一起重新扫描一遍——这里选择
        # "可能重复通知"而不是"可能永久漏记"（PR #99 复审第三轮 P2）。
        watermark = cutoff if failed_sends == 0 else last
        return crud.encode_import_notify_output(summary, watermark)

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

    async def disk_check() -> str:
        percent = resources.disk_usage_percent()
        critical = percent >= resources.DISK_CRIT_PERCENT
        is_alerting = percent >= resources.DISK_WARN_PERCENT
        # 告警档只按天提醒，escalate_after=0 关掉"持续异常"强提醒（连续计数从 1 起，
        # 永远不等于 0），否则持续 45 分钟时仍会多发一条。
        severity = (
            {
                "level": "crit",
                "silence_minutes": settings.OPS_ALERT_SILENCE_MINUTES,
            }
            if critical
            else {
                "level": "warn",
                "silence_minutes": resources.DISK_WARN_REMINDER_MINUTES,
                "escalate_after": 0,
            }
        )
        async with OpsAsyncSessionFactory() as session:
            await alerts.evaluate(
                session, feishu_client, chat_id,
                alert_key="disk-usage", is_alerting=is_alerting,
                message=f"磁盘使用率 {percent:.1f}%",
                **severity,
            )
            await session.commit()
        return f"{percent:.1f}%"

    async def alembic_drift_check() -> str:
        # is_in_sync() 内部跑两个各 60 秒超时的 `docker compose run` 子进程，直接
        # 同步调用会占住事件循环——探测卡住时 /ops/health、飞书回调、心跳调度全部
        # 无法响应，最长可达两分钟。与 backup/git_ref 的既有约定一致，丢进线程池
        # （PR #99 复审第二轮 P2）。
        in_sync = await asyncio.to_thread(alembic_drift.is_in_sync)
        # `in_sync is None` 是探测本身失败（容器起不来、超时），不是"已同步"——
        # `in_sync is False` 会让探测失败被判定为不告警，一次真正的漂移探测失败
        # 因此被静默放过，与 alembic_drift.is_in_sync() 文档字符串明确的契约相反
        # （PR #99 复审第二轮 P2，与上面 pool_check 同一条纪律）。
        is_alerting = in_sync is None or in_sync is False
        async with OpsAsyncSessionFactory() as session:
            await alerts.evaluate(
                session, feishu_client, chat_id,
                alert_key="alembic-drift", level="crit", is_alerting=is_alerting,
                message=(
                    "生产库 alembic 版本与代码不一致（current != heads）"
                    if in_sync is False
                    else "alembic 漂移探测失败（未知状态，按异常处理）"
                ),
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

    async def _report_retention(*, failed: bool, failure: str, last: dict | None) -> None:
        async with OpsAsyncSessionFactory() as session:
            await alerts.evaluate(
                session, feishu_client, chat_id,
                alert_key="retention-job-failed", level="crit", is_alerting=failed,
                message=failure or "保留清理任务",
                silence_minutes=settings.OPS_ALERT_SILENCE_MINUTES,
            )
            # 没有拿到结果（运行失败、导入占锁）时不知道归档状态，不能把既有拦截告警判成已恢复。
            if last is not None and last["status"] == "ok":
                await alerts.evaluate(
                    session, feishu_client, chat_id,
                    alert_key="retention-gate-blocked", level="warn",
                    is_alerting=bool(last.get("blocked")),
                    message=_retention_blocked_message(last),
                    silence_minutes=settings.OPS_ALERT_SILENCE_MINUTES,
                )
            await session.commit()

    async def retention_prune_job() -> str:
        # 任何失败都在这里收口并正常返回：调度器遇异常记 FAILED，而 daily_at 只看
        # 最近一次成功，抛出去会让这个删除任务每 60 秒重跑一次（design D8）。
        summaries: list[str] = []
        last: dict | None = None
        failure = ""
        try:
            last = await asyncio.to_thread(retention.run_prune, dry_run=True)
            summaries.append(_summarize_retention("预览", last))
            logger.info("保留清理%s", summaries[-1])
            if settings.OPS_RETENTION_MODE == "execute":
                last = await asyncio.to_thread(retention.run_prune, dry_run=False)
                summaries.append(_summarize_retention("执行", last))
                logger.info("保留清理%s", summaries[-1])
        except retention.RetentionRunError as exc:
            failure = f"保留清理失败：{exc}"
            summaries.append(failure)
            logger.error(failure)
        try:
            await _report_retention(failed=bool(failure), failure=failure, last=last)
        except Exception:
            logger.exception("保留清理结果告警失败")
            summaries.append("告警发送失败，详见应用日志")
        return "；".join(summaries)

    retention_jobs = (
        []
        if settings.OPS_RETENTION_MODE == "off"
        else [
            ScheduledJob(
                kind="RETENTION_PRUNE", interval_seconds=86400, run=retention_prune_job,
                should_run=daily_at(3, 30),
            )
        ]
    )

    return [
        *retention_jobs,
        ScheduledJob(kind=heartbeat.KIND, interval_seconds=_QUICK_SECONDS, run=heartbeat.run),
        ScheduledJob(kind="SERVICE_CHECK", interval_seconds=_FULL_SECONDS, run=service_check),
        ScheduledJob(kind="POOL_CHECK", interval_seconds=_FULL_SECONDS, run=pool_check),
        ScheduledJob(kind="IMPORT_STUCK_CHECK", interval_seconds=_FULL_SECONDS, run=import_stuck_check),
        ScheduledJob(kind="IMPORT_NOTIFY", interval_seconds=_FULL_SECONDS, run=import_notify),
        ScheduledJob(kind="LOG_ERROR_RATE_CHECK", interval_seconds=_FULL_SECONDS, run=log_error_rate_check),
        ScheduledJob(kind="DISK_CHECK", interval_seconds=_FIFTEEN_MIN_SECONDS, run=disk_check),
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
