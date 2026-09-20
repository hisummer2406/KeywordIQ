"""ops-bot webhook/health 入口的单元测试。

不用 `with TestClient(...)` 上下文管理器，因此不进入任何 lifespan（不起调度循环）
——同构 `test_monitoring_endpoint_unit.py` 的做法。`/ops/health` 仍会查一次
`ops.ops_task`，用 `AsyncMock` 顶掉 `crud.last_succeeded_at` 避免真连库；带 DB 的
幂等/鉴权分支覆盖在 `test_ops_bot_crud_integration.py`（幂等）与本文件之外的鉴权
单元测试（`is_authorized` 纯函数，见 `test_ops_bot_feishu_unit.py`）里。
"""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from ops_bot import main


def _client() -> TestClient:
    return TestClient(main.app, raise_server_exceptions=False)


class TestOpsHealth(unittest.TestCase):
    def test_no_heartbeat_yet_reports_starting(self):
        with patch("ops_bot.main.crud.last_succeeded_at", new=AsyncMock(return_value=None)):
            response = _client().get("/ops/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "starting"})

    def test_fresh_heartbeat_reports_ok(self):
        recent = datetime.now(UTC) - timedelta(seconds=5)
        with patch("ops_bot.main.crud.last_succeeded_at", new=AsyncMock(return_value=recent)):
            response = _client().get("/ops/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_stale_heartbeat_returns_503(self):
        """心跳超过容忍窗口未更新——区分「循环卡死」与「进程还能响应 HTTP」的关键路径。"""
        stale = datetime.now(UTC) - timedelta(seconds=main._HEARTBEAT_STALE_AFTER_SECONDS + 1)
        with patch("ops_bot.main.crud.last_succeeded_at", new=AsyncMock(return_value=stale)):
            response = _client().get("/ops/health")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "stale")

    def test_database_unreachable_returns_503_not_500(self):
        """`ops` schema 与业务 schema 同处一个自建 PG 实例，实例整体不可达时这个
        端点不能裸抛 500——探测方需要一个成形的"数据库不可达"结论。
        """
        with patch(
            "ops_bot.main.crud.last_succeeded_at", new=AsyncMock(side_effect=OSError("连接失败"))
        ):
            response = _client().get("/ops/health")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "db_unreachable")


class TestFeishuWebhookUrlVerification(unittest.TestCase):
    def test_challenge_echoed_without_touching_db(self):
        with patch("ops_bot.main.settings") as mock_settings:
            mock_settings.FEISHU_VERIFICATION_TOKEN = "tok"
            mock_settings.FEISHU_ENCRYPT_KEY = ""

            response = _client().post(
                "/ops/feishu/webhook",
                content=json.dumps({"type": "url_verification", "token": "tok", "challenge": "abc123"}),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"challenge": "abc123"})

    def test_wrong_token_rejected_with_403(self):
        with patch("ops_bot.main.settings") as mock_settings:
            mock_settings.FEISHU_VERIFICATION_TOKEN = "tok"
            mock_settings.FEISHU_ENCRYPT_KEY = ""

            response = _client().post(
                "/ops/feishu/webhook",
                content=json.dumps({"type": "url_verification", "token": "wrong", "challenge": "abc"}),
            )

        self.assertEqual(response.status_code, 403)

    def test_malformed_body_rejected_with_403(self):
        with patch("ops_bot.main.settings") as mock_settings:
            mock_settings.FEISHU_VERIFICATION_TOKEN = "tok"
            mock_settings.FEISHU_ENCRYPT_KEY = ""

            response = _client().post("/ops/feishu/webhook", content=b"not json")

        self.assertEqual(response.status_code, 403)


class TestFeishuWebhookAuthorization(unittest.TestCase):
    def test_unconfigured_whitelist_rejects_silently_but_logs_ids(self):
        """白名单没配（或配错）时对方收不到任何回复，日志是唯一线索：必须带上 open_id/chat_id。"""
        event = {
            "header": {"token": "tok"},  # 无 event_id：跳过去重，不碰数据库
            "event": {
                "sender": {"sender_id": {"open_id": "ou_someone"}},
                "message": {"chat_id": "oc_somechat", "content": json.dumps({"text": "状态"})},
            },
        }
        with patch("ops_bot.main.settings") as mock_settings:
            mock_settings.FEISHU_VERIFICATION_TOKEN = "tok"
            mock_settings.FEISHU_ENCRYPT_KEY = ""
            mock_settings.FEISHU_OWNER_OPEN_ID = ""
            mock_settings.FEISHU_OPS_CHAT_ID = ""
            with (
                patch("ops_bot.main.commands.dispatch", new=AsyncMock()) as dispatch,
                self.assertLogs("ops_bot.main", level="WARNING") as logs,
            ):
                response = _client().post("/ops/feishu/webhook", content=json.dumps(event))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")
        dispatch.assert_not_awaited()
        self.assertIn("ou_someone", logs.output[0])
        self.assertIn("oc_somechat", logs.output[0])


class TestRunManualBackup(unittest.IsolatedAsyncioTestCase):
    """`_run_manual_backup` 是后台任务体，不经 HTTP 层——单独测，覆盖成功/失败两条路径
    各自正确落 `ops_task` 并发出对应的结果消息。
    """

    async def test_success_marks_succeeded_and_sends_result(self):
        mark_succeeded = AsyncMock()
        mark_failed = AsyncMock()
        send_text = AsyncMock()

        with patch("ops_bot.main.crud.create_task", new=AsyncMock(return_value="t-1")), patch(
            "ops_bot.main.crud.mark_task_succeeded", mark_succeeded
        ), patch("ops_bot.main.crud.mark_task_failed", mark_failed), patch(
            "ops_bot.main.backup.run_backup", new=AsyncMock(return_value="备份成功：xxx")
        ), patch("ops_bot.main.OpsAsyncSessionFactory") as factory, patch(
            "ops_bot.main._feishu_client.send_text", send_text
        ):
            factory.return_value.__aenter__.return_value = AsyncMock()
            await main._run_manual_backup("ou_owner")

        mark_succeeded.assert_awaited_once()
        mark_failed.assert_not_awaited()
        self.assertIn("✅", send_text.call_args.args[1])

    async def test_failure_marks_failed_and_sends_error(self):
        mark_succeeded = AsyncMock()
        mark_failed = AsyncMock()
        send_text = AsyncMock()

        with patch("ops_bot.main.crud.create_task", new=AsyncMock(return_value="t-1")), patch(
            "ops_bot.main.crud.mark_task_succeeded", mark_succeeded
        ), patch("ops_bot.main.crud.mark_task_failed", mark_failed), patch(
            "ops_bot.main.backup.run_backup", new=AsyncMock(side_effect=RuntimeError("boom"))
        ), patch("ops_bot.main.OpsAsyncSessionFactory") as factory, patch(
            "ops_bot.main._feishu_client.send_text", send_text
        ):
            factory.return_value.__aenter__.return_value = AsyncMock()
            await main._run_manual_backup("ou_owner")

        mark_failed.assert_awaited_once()
        mark_succeeded.assert_not_awaited()
        self.assertIn("❌", send_text.call_args.args[1])


class TestHandleCardAction(unittest.IsolatedAsyncioTestCase):
    async def test_unauthorized_open_id_ignored(self):
        with patch("ops_bot.main.settings") as mock_settings:
            mock_settings.FEISHU_OWNER_OPEN_ID = "ou_owner"
            mock_settings.FEISHU_OPS_CHAT_ID = "oc_ops"
            with patch("ops_bot.main.release_flow.handle_confirm_callback") as mock_handle:
                response = await main._handle_card_action(
                    {"open_id": "ou_evil", "open_chat_id": "oc_ops", "action": {"value": {}}}
                )

        mock_handle.assert_not_called()
        self.assertEqual(response.status_code, 200)

    async def test_unauthorized_rejection_logs_ids_at_warning(self):
        """INFO 到不了 journal，拒绝必须是 WARNING 且带上 ID，否则首次部署没法取到这两个值。"""
        with patch("ops_bot.main.settings") as mock_settings:
            mock_settings.FEISHU_OWNER_OPEN_ID = "ou_owner"
            mock_settings.FEISHU_OPS_CHAT_ID = "oc_ops"
            with self.assertLogs("ops_bot.main", level="WARNING") as logs:
                await main._handle_card_action(
                    {"open_id": "ou_evil", "open_chat_id": "oc_other", "action": {"value": {}}}
                )

        self.assertIn("ou_evil", logs.output[0])
        self.assertIn("oc_other", logs.output[0])

    async def test_immediate_reply_when_no_execution_needed(self):
        """快速失败路径（已取消/已过期/已处理……）：直接回复，不碰后台任务。"""
        from ops_bot.release.flow import ConfirmOutcome

        outcome = ConfirmOutcome("已取消", None)
        with patch("ops_bot.main.settings") as mock_settings:
            mock_settings.FEISHU_OWNER_OPEN_ID = "ou_owner"
            mock_settings.FEISHU_OPS_CHAT_ID = "oc_ops"
            with patch(
                "ops_bot.main.release_flow.handle_confirm_callback",
                new=AsyncMock(return_value=outcome),
            ) as mock_handle, patch("ops_bot.main._reply", new=AsyncMock()) as mock_reply, patch(
                "ops_bot.main._spawn_background"
            ) as mock_spawn:
                response = await main._handle_card_action(
                    {
                        "open_id": "ou_owner",
                        "open_chat_id": "oc_ops",
                        "action": {"value": {"task_id": "t-1", "confirm_token": "tok-1", "decision": "cancel"}},
                    }
                )

        mock_handle.assert_awaited_once_with("t-1", "tok-1", "cancel")
        mock_reply.assert_awaited_once_with("已取消")
        mock_spawn.assert_not_called()
        self.assertEqual(response.status_code, 200)

    async def test_confirmed_action_acks_immediately_and_runs_in_background(self):
        """执行必须移交给后台任务，回调请求本身立刻回执——不能等重启/发布跑完才
        返回，否则会同时超过飞书重试窗口与 nginx 反代超时（PR #99 Codex 复审 P1）。
        """
        from ops_bot.release.flow import ConfirmOutcome

        outcome = ConfirmOutcome(None, AsyncMock(return_value="✅ 重启应用成功"))
        with patch("ops_bot.main.settings") as mock_settings:
            mock_settings.FEISHU_OWNER_OPEN_ID = "ou_owner"
            mock_settings.FEISHU_OPS_CHAT_ID = "oc_ops"
            with patch(
                "ops_bot.main.release_flow.handle_confirm_callback",
                new=AsyncMock(return_value=outcome),
            ) as mock_handle, patch("ops_bot.main._reply", new=AsyncMock()) as mock_reply, patch(
                "ops_bot.main._spawn_background"
            ) as mock_spawn:
                response = await main._handle_card_action(
                    {
                        "open_id": "ou_owner",
                        "open_chat_id": "oc_ops",
                        "action": {"value": {"task_id": "t-1", "confirm_token": "tok-1", "decision": "confirm"}},
                    }
                )

                # 请求协程本身立刻回执"受理中"，不等待执行完成。
                mock_handle.assert_awaited_once_with("t-1", "tok-1", "confirm")
                mock_reply.assert_awaited_once_with("已受理，任务 #t-1 执行中…")
                self.assertEqual(response.status_code, 200)
                mock_spawn.assert_called_once()

                # 手动跑一遍被移交给后台的协程，验证它最终把 run() 的结果发出去——
                # 这一步不在请求协程里发生，正是这个修复要保证的。
                background_coro = mock_spawn.call_args.args[0]
                mock_reply.reset_mock()
                await background_coro

        mock_reply.assert_awaited_once_with("✅ 重启应用成功")

    async def test_missing_value_fields_ignored(self):
        with patch("ops_bot.main.settings") as mock_settings:
            mock_settings.FEISHU_OWNER_OPEN_ID = "ou_owner"
            mock_settings.FEISHU_OPS_CHAT_ID = "oc_ops"
            with patch("ops_bot.main.release_flow.handle_confirm_callback") as mock_handle:
                response = await main._handle_card_action(
                    {"open_id": "ou_owner", "open_chat_id": "oc_ops", "action": {"value": {"task_id": "t-1"}}}
                )

        mock_handle.assert_not_called()
        self.assertEqual(response.status_code, 200)


class TestDeployDispatch(unittest.TestCase):
    """`发布 <sha>` 指令必须立刻回执、把 `build_deploy_plan`（内含 `git fetch`/CI 查询/
    好几个 git 子进程）移交后台执行，不能同步等它跑完（PR #99 复审第三轮 P2）。
    """

    def test_acks_immediately_and_defers_plan_building_to_background(self):
        target_sha = "a" * 40
        with patch("ops_bot.main.settings") as mock_settings:
            mock_settings.FEISHU_VERIFICATION_TOKEN = "tok"
            mock_settings.FEISHU_ENCRYPT_KEY = ""
            mock_settings.FEISHU_OWNER_OPEN_ID = "ou_owner"
            mock_settings.FEISHU_OPS_CHAT_ID = "oc_ops"

            with patch(
                "ops_bot.main.crud.try_claim_dedup_key", new=AsyncMock(return_value=True)
            ), patch("ops_bot.main.OpsAsyncSessionFactory") as factory, patch(
                "ops_bot.main.release_flow.build_deploy_plan"
            ) as mock_build_plan, patch(
                "ops_bot.main._reply", new=AsyncMock()
            ) as mock_reply, patch("ops_bot.main._spawn_background") as mock_spawn:
                factory.return_value.__aenter__.return_value = AsyncMock()

                response = _client().post(
                    "/ops/feishu/webhook",
                    content=json.dumps(
                        {
                            "token": "tok",
                            "header": {"event_id": "evt-1"},
                            "event": {
                                "sender": {"sender_id": {"open_id": "ou_owner"}},
                                "message": {
                                    "chat_id": "oc_ops",
                                    "content": json.dumps({"text": f"发布 {target_sha}"}),
                                },
                            },
                        }
                    ),
                )

        self.assertEqual(response.status_code, 200)
        # 请求协程本身立刻回执"受理中"，不等 build_deploy_plan 跑完。
        mock_reply.assert_awaited_once_with("已受理发布请求，正在核实 CI/SHA/迁移边界…")
        mock_build_plan.assert_not_called()
        mock_spawn.assert_called_once()
        # 移交后台的协程本身不在这个测试里跑（那是 TestBuildAndSendDeployPlan 的
        # 职责），关掉它只是避免"coroutine was never awaited"的噪音警告。
        mock_spawn.call_args.args[0].close()


class TestBuildAndSendDeployPlan(unittest.IsolatedAsyncioTestCase):
    async def test_builds_plan_and_sends_result(self):
        """手动跑一遍被移交给后台的协程，验证它最终确实调用了 `build_deploy_plan`
        并把结果发出去——这一步不在请求协程里发生，正是 `TestDeployDispatch`
        那个修复要保证的。
        """
        with patch(
            "ops_bot.main.release_flow.build_deploy_plan",
            new=AsyncMock(return_value=(None, "CI 未全部通过：backend: FAILED")),
        ) as mock_build_plan, patch("ops_bot.main._send_plan_or_reason", new=AsyncMock()) as mock_send:
            await main._build_and_send_deploy_plan("a" * 40, "ou_owner")

        mock_build_plan.assert_awaited_once_with("a" * 40, "ou_owner")
        mock_send.assert_awaited_once_with(None, "CI 未全部通过：backend: FAILED")

    async def test_exception_during_build_still_replies_instead_of_hanging_silently(self):
        """`build_deploy_plan` 抛出未预期异常（运维库暂时不可用等）时必须回一条终态
        消息——这个协程是后台 fire-and-forget 任务，异常不会被任何地方转发，用户
        此前已经收到"正在核实…"，不兜底会让用户永远停在这句话上（PR #99 复审
        第四轮 P2）。
        """
        with patch(
            "ops_bot.main.release_flow.build_deploy_plan",
            new=AsyncMock(side_effect=RuntimeError("db unavailable")),
        ), patch("ops_bot.main._send_plan_or_reason", new=AsyncMock()) as mock_send, patch(
            "ops_bot.main._reply", new=AsyncMock()
        ) as mock_reply:
            await main._build_and_send_deploy_plan("a" * 40, "ou_owner")

        mock_send.assert_not_awaited()
        mock_reply.assert_awaited_once()
        self.assertIn("异常", mock_reply.call_args.args[0])


class TestReconcileOrphanedTasksOnStartup(unittest.IsolatedAsyncioTestCase):
    """接受新任务前必须先回收上一次进程遗留的 RUNNING/CONFIRMED 任务，否则
    `任务` 指令会把它们永远显示成"进行中"（PR #99 复审第三轮 P2）。
    """

    async def test_calls_reconcile_and_logs_when_something_was_recovered(self):
        with patch("ops_bot.main.OpsAsyncSessionFactory") as factory, patch(
            "ops_bot.main.crud.reconcile_orphaned_tasks", new=AsyncMock(return_value=2)
        ) as mock_reconcile, patch("ops_bot.main.logger") as mock_logger:
            session = AsyncMock()
            factory.return_value.__aenter__.return_value = session

            await main._reconcile_orphaned_tasks_on_startup()

        mock_reconcile.assert_awaited_once()
        self.assertEqual(mock_reconcile.call_args.args[0], session)
        session.commit.assert_awaited_once()
        mock_logger.warning.assert_called_once()

    async def test_no_warning_when_nothing_to_recover(self):
        with patch("ops_bot.main.OpsAsyncSessionFactory") as factory, patch(
            "ops_bot.main.crud.reconcile_orphaned_tasks", new=AsyncMock(return_value=0)
        ), patch("ops_bot.main.logger") as mock_logger:
            factory.return_value.__aenter__.return_value = AsyncMock()

            await main._reconcile_orphaned_tasks_on_startup()

        mock_logger.warning.assert_not_called()


class TestRetireAlertKeysOnStartup(unittest.IsolatedAsyncioTestCase):
    """已退役巡检遗留的未解决告警要在启动时自动关闭，不能靠人工 SQL；但这只是清理
    展示用的残留记录，失败不能让机器人起不来。"""

    async def test_retires_the_configured_keys_and_commits(self):
        with patch("ops_bot.main.OpsAsyncSessionFactory") as factory, patch(
            "ops_bot.main.crud.retire_alert_keys", new=AsyncMock(return_value=1)
        ) as mock_retire:
            session = AsyncMock()
            factory.return_value.__aenter__.return_value = session

            await main._retire_alert_keys_on_startup()

        mock_retire.assert_awaited_once()
        self.assertEqual(mock_retire.call_args.args[0], session)
        self.assertIn("backup-overdue", mock_retire.call_args.args[1])
        session.commit.assert_awaited_once()

    async def test_failure_is_logged_and_does_not_raise(self):
        with patch("ops_bot.main.OpsAsyncSessionFactory") as factory, patch(
            "ops_bot.main.crud.retire_alert_keys", new=AsyncMock(side_effect=RuntimeError("db down"))
        ), patch("ops_bot.main.logger") as mock_logger:
            factory.return_value.__aenter__.return_value = AsyncMock()

            await main._retire_alert_keys_on_startup()  # 不抛

        mock_logger.exception.assert_called_once()


class TestDispatchExceptionFallback(unittest.TestCase):
    """`ops` schema 与业务 schema 同处一个自建 PG 实例，指令分发链路里任何一步
    数据库查询失败都可能抛出未预期异常——这条兜底必须把它接住，否则事件已经被
    标记"已处理"（dedup key 已落库），用户的指令会得不到任何回应且无从排查。
    """

    def test_unhandled_exception_replies_gracefully_and_returns_200(self):
        with patch("ops_bot.main.settings") as mock_settings:
            mock_settings.FEISHU_VERIFICATION_TOKEN = "tok"
            mock_settings.FEISHU_ENCRYPT_KEY = ""
            mock_settings.FEISHU_OWNER_OPEN_ID = "ou_owner"
            mock_settings.FEISHU_OPS_CHAT_ID = "oc_ops"

            with patch(
                "ops_bot.main.crud.try_claim_dedup_key", new=AsyncMock(return_value=True)
            ), patch("ops_bot.main.OpsAsyncSessionFactory") as factory, patch(
                "ops_bot.main.commands.dispatch", new=AsyncMock(side_effect=OSError("db down"))
            ), patch("ops_bot.main._reply", new=AsyncMock()) as mock_reply:
                factory.return_value.__aenter__.return_value = AsyncMock()

                response = _client().post(
                    "/ops/feishu/webhook",
                    content=json.dumps(
                        {
                            "token": "tok",
                            "header": {"event_id": "evt-1"},
                            "event": {
                                "sender": {"sender_id": {"open_id": "ou_owner"}},
                                "message": {"chat_id": "oc_ops", "content": json.dumps({"text": "状态"})},
                            },
                        }
                    ),
                )

        self.assertEqual(response.status_code, 200)
        mock_reply.assert_awaited_once()
        self.assertIn("处理失败", mock_reply.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
