"""重启/发布的确认状态机：生成确认卡片 → 处理确认回调 → 执行前重新核实 → 执行 →
回报结果（design.md D5）。

**执行前重新核实，不信任确认时刻的快照**——CI 状态、SHA 归属、导入是否空闲都在
真正执行的这一刻重新查一遍；确认卡片展示的是生成那一刻的判断，人工确认可能过去
了好几分钟，期间任何一项都可能已经变化。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from config import settings
from ops_bot import alerts, crud, executor
from ops_bot.checks import import_status
from ops_bot.database import OpsAsyncSessionFactory
from ops_bot.feishu import cards
from ops_bot.model import TASK_PENDING_CONFIRM
from ops_bot.release import ci, git_ref, lock

logger = logging.getLogger(__name__)

# 重启/发布期间可能触发的巡检告警键，执行开始时一并打开维护窗口——
# 与 alerts.py 的键名保持一致（design.md D3「维护窗口」）。
_MAINTENANCE_ALERT_KEYS = ("service-down",)


@dataclass
class ConfirmPlan:
    task_id: str
    card: dict


@dataclass
class ConfirmOutcome:
    """`handle_confirm_callback` 的结果。

    `immediate_reply` 非 `None` 时是终态（校验失败/已取消/已过期/并发丢锁），
    调用方直接把这段文本回给用户即可，`run` 为 `None`。

    `immediate_reply` 为 `None` 时说明已经通过校验、任务状态已经落为
    `CONFIRMED`，真正的执行打包在 `run` 里——**调用方必须把它丢到后台跑，
    不能在处理这次卡片回调的协程里直接 `await` 它**：执行可能跑到 executor
    的 1800 秒超时，同步等完会让这次回调既超过飞书的重试窗口、也超过 nginx
    反代超时，飞书只会看到超时重试而收不到任何响应（PR #99 Codex 复审 P1）。
    """

    immediate_reply: str | None
    run: Callable[[], Awaitable[str]] | None = None


async def _import_active_or_unknown(session) -> bool:
    """`True` 表示"应视为不空闲"——查询失败也算不空闲，design.md 要求"取不到任务
    状态时不得当作空闲"。
    """
    try:
        return await import_status.any_active(session)
    except Exception:
        logger.exception("查询导入状态失败，按不空闲处理")
        return True


async def build_restart_plan(requested_by_open_id: str | None) -> tuple[ConfirmPlan | None, str | None]:
    async with OpsAsyncSessionFactory() as session:
        if await _import_active_or_unknown(session):
            return None, "导入进行中，暂不可发起重启"
        task_id, token = await crud.create_pending_confirm_task(
            session, kind="RESTART_APP", payload={},
            requested_by_open_id=requested_by_open_id, ttl_minutes=settings.OPS_CONFIRM_TTL_MINUTES,
        )
        await session.commit()

    card = cards.build_confirm_card(
        action="重启应用", task_id=task_id, confirm_token=token,
        summary_lines=["目标：docker compose restart app", f"有效期：{settings.OPS_CONFIRM_TTL_MINUTES} 分钟"],
    )
    return ConfirmPlan(task_id, card), None


async def build_deploy_plan(
    target_sha: str, requested_by_open_id: str | None
) -> tuple[ConfirmPlan | None, str | None]:
    if not git_ref.is_valid_sha_format(target_sha):
        return None, "目标必须是完整的 40 位 commit SHA，不接受分支名或短 SHA"

    is_ancestor = await asyncio.to_thread(git_ref.is_ancestor_of_branch, target_sha)
    if is_ancestor is not True:
        return None, "目标 SHA 不在允许分支（v2）的历史上，或无法核实"

    ci_result = await ci.check(target_sha)
    if not ci_result.passing:
        detail = "；".join(f"{k}: {v}" for k, v in ci_result.details.items())
        return None, f"CI 未全部通过：{detail}"

    needs_review = await asyncio.to_thread(git_ref.rollback_needs_review, target_sha)
    if needs_review is None:
        return None, "无法核实回滚边界，暂不可发起发布"
    if needs_review:
        return None, "目标版本落后于当前数据库结构，不支持自动降级数据库，需人工评估后手动处理"

    with_migration = await asyncio.to_thread(git_ref.migrations_changed, target_sha)
    if with_migration is None:
        return None, "无法核实是否包含数据库迁移，暂不可发起发布"

    async with OpsAsyncSessionFactory() as session:
        if await _import_active_or_unknown(session):
            return None, "导入进行中，暂不可发起发布"
        payload = {"target_sha": target_sha, "with_migration": with_migration}
        task_id, token = await crud.create_pending_confirm_task(
            session, kind="DEPLOY", payload=payload,
            requested_by_open_id=requested_by_open_id, ttl_minutes=settings.OPS_CONFIRM_TTL_MINUTES,
        )
        await session.commit()

    current = await asyncio.to_thread(git_ref.current_sha)
    changes = await asyncio.to_thread(git_ref.change_summary, current, target_sha) if current else None

    summary = [
        f"当前 SHA：{current[:12] if current else '（无法核实）'}",
        f"目标 SHA：{target_sha[:12]}",
        "CI：" + "；".join(f"{k}={v}" for k, v in ci_result.details.items()),
        f"数据库迁移：{'是' if with_migration else '否'}",
        f"有效期：{settings.OPS_CONFIRM_TTL_MINUTES} 分钟",
    ]
    if changes:
        summary.append("变更：" + "；".join(changes))
    card = cards.build_confirm_card(action="发布", task_id=task_id, confirm_token=token, summary_lines=summary)
    return ConfirmPlan(task_id, card), None


async def handle_confirm_callback(task_id: str, confirm_token: str, decision: str) -> ConfirmOutcome:
    """处理卡片按钮回调。校验与状态转移都是快速的数据库操作，同步做完；真正的
    执行（可能耗时到分钟级）不在这里 await，打包进返回值的 `run` 交给调用方
    在后台跑（见 :class:`ConfirmOutcome`）。
    """
    async with OpsAsyncSessionFactory() as session:
        task = await crud.get_task(session, task_id)
        if task is None or task.confirm_token != confirm_token:
            return ConfirmOutcome("该确认已失效，请重新发起")
        if task.status != TASK_PENDING_CONFIRM:
            return ConfirmOutcome("该任务已处理或已过期")
        if task.confirm_expires_at is not None and datetime.now(UTC) > task.confirm_expires_at:
            await crud.mark_task_expired(session, task_id)
            await session.commit()
            return ConfirmOutcome("已过期，请重新发起")

        if decision != "confirm":
            cancelled = await crud.try_cancel_task(session, task_id, output="用户取消")
            await session.commit()
            if not cancelled:
                # 与确认按钮之间存在竞态窗口（双击、飞书重放、或确认已抢先落地并已经
                # 把执行 closure 交给后台跑）——乐观锁兜底，不能无条件覆盖任务状态
                # （PR #99 复审第三轮 P1，见 crud.try_cancel_task 文档字符串）。
                return ConfirmOutcome("该任务已处理或已过期")
            return ConfirmOutcome("已取消")

        confirmed = await crud.try_confirm_task(session, task_id)
        await session.commit()
        if not confirmed:
            # 与上面的状态检查之间存在竞态窗口（双击、飞书重放）——乐观锁兜底。
            return ConfirmOutcome("该任务已处理或已过期")

        kind, payload = task.kind, dict(task.payload)
        actor_open_id = task.requested_by_open_id

    confirmed_at = datetime.now(UTC)

    async def run() -> str:
        return await _execute_confirmed(task_id, kind, payload, actor_open_id, confirmed_at)

    return ConfirmOutcome(None, run)


async def _execute_confirmed(
    task_id: str, kind: str, payload: dict, actor_open_id: str | None, confirmed_at: datetime
) -> str:
    target = "docker compose restart app" if kind == "RESTART_APP" else "deploy.sh"
    target_sha = payload.get("target_sha")

    async def _finish(success: bool, output: str, failed_stage: str | None = None) -> str:
        async with OpsAsyncSessionFactory() as session:
            if success:
                await crud.mark_task_succeeded(session, task_id, output=output)
            else:
                await crud.mark_task_failed(session, task_id, output=output)
            await crud.write_audit_log(
                session,
                task_id=task_id,
                actor_open_id=actor_open_id,
                action=kind,
                target=target,
                target_sha=target_sha,
                confirmed_at=confirmed_at,
                result="SUCCEEDED" if success else "FAILED",
                failed_stage=failed_stage,
            )
            await session.commit()
        prefix = "✅" if success else "❌"
        return f"{prefix} {output}"

    async with OpsAsyncSessionFactory() as session:
        if await _import_active_or_unknown(session):
            return await _finish(False, "执行前重新核实：导入进行中", "PRECHECK_IMPORT")
        await crud.mark_task_running(session, task_id)
        await session.commit()

    if kind == "DEPLOY":
        ci_result = await ci.check(target_sha)
        if not ci_result.passing:
            return await _finish(False, "执行前重新核实：CI 未通过", "PRECHECK_CI")
        is_ancestor = await asyncio.to_thread(git_ref.is_ancestor_of_branch, target_sha)
        if is_ancestor is not True:
            return await _finish(False, "执行前重新核实：SHA 已不在允许分支历史上", "PRECHECK_SHA")

    async with lock.try_acquire() as got_lock:
        if not got_lock:
            # 抢不到这把锁有两种成因：另一个重启/发布正握着它，或者导入侧正在
            # `_claim_processing_slot`（app/table/upload/import_service.py）里把
            # 一个批次转场为 PROCESSING——那一步现在也短暂持有同一把锁，不再是
            # "探测后立即释放"。两种情况都应中止，等锁释放后重新发起即可；抢不到
            # 时无法区分是谁持有，消息按更常见的重启/发布互斥场景措辞。
            return await _finish(False, "另一个重启/发布任务正在执行中，已中止", "MUTEX")

        # 持锁期间复核一次导入状态——上面那次检查（第 174 行附近）与这里之间存在
        # 窗口：导入可能已经在这期间认领了一个批次。只有在持锁期间做的这次复核
        # 才是真正的互斥边界，之前那次只是快速失败的优化，不能单独作为互斥保证
        # （design.md D5，PR #99 Codex 复审 P1）。
        async with OpsAsyncSessionFactory() as session:
            if await _import_active_or_unknown(session):
                return await _finish(False, "执行前重新核实：导入进行中", "PRECHECK_IMPORT")

        if kind == "DEPLOY":
            # 同一条道理适用于迁移/回滚边界，不只是导入状态：两张发布卡片可以在
            # 同一个 checkout 上先后生成（互斥锁只保证不并发执行，不保证不先后
            # 执行），先确认的那张若带迁移先执行完，这张卡片的 `payload["with_migration"]`
            # 就是基于执行前旧 checkout 算出的过期值——带着它直接跑会让
            # `executor.deploy` 传出错误的 `--skip-migrate`/`--migrate`，用旧代码
            # 部署到已经前进的 schema 上而不触发本该拦它的回滚复核（PR #99
            # 复审第二轮 P1）。持锁期间按当前 HEAD 重新算一遍，任何一项与确认时
            # 不一致就中止，要求重新发起——不能静默改用新值替确认过的人做决定。
            needs_review = await asyncio.to_thread(git_ref.rollback_needs_review, target_sha)
            if needs_review is None:
                return await _finish(False, "执行前重新核实：无法核实回滚边界", "PRECHECK_MIGRATION")
            if needs_review:
                return await _finish(
                    False, "执行前重新核实：目标版本已落后于当前数据库结构，需人工评估", "PRECHECK_MIGRATION"
                )
            fresh_with_migration = await asyncio.to_thread(git_ref.migrations_changed, target_sha)
            if fresh_with_migration is None:
                return await _finish(False, "执行前重新核实：无法核实是否包含数据库迁移", "PRECHECK_MIGRATION")
            if fresh_with_migration != payload["with_migration"]:
                return await _finish(
                    False, "执行前重新核实：迁移状态与确认时不一致，请重新发起", "PRECHECK_MIGRATION"
                )

        await _open_maintenance()

        if kind == "RESTART_APP":
            action_label = "重启应用"
            result = await asyncio.to_thread(executor.restart_app)
        else:
            action_label = "发布"
            plan = executor.DeployPlan(target_sha=target_sha, with_migration=payload["with_migration"])
            result = await asyncio.to_thread(executor.deploy, plan)

    status_word = "成功" if result.success else "失败"
    return await _finish(result.success, f"{action_label}{status_word}：{result.output[-500:]}", result.failed_stage)


async def _open_maintenance() -> None:
    async with OpsAsyncSessionFactory() as session:
        await alerts.open_maintenance_window(
            session, list(_MAINTENANCE_ALERT_KEYS), minutes=settings.OPS_MAINTENANCE_WINDOW_MINUTES
        )
        await session.commit()
