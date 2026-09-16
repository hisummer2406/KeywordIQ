"""重启/发布确认状态机的单元测试：打桩 crud/ci/git_ref/executor/lock，只验证控制流。

这是安全关键路径（未确认不执行、过期/重复确认无副作用、执行前重新核实、
互斥），覆盖尽量穷举 design.md D5 列出的每条约束对应的分支。
"""

from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from ops_bot.executor import ExecutionResult
from ops_bot.model import OpsTask
from ops_bot.release import flow


@asynccontextmanager
async def _fake_session_ctx():
    yield AsyncMock()


@asynccontextmanager
async def _lock_ctx(got: bool):
    yield got


def _task(**overrides) -> OpsTask:
    defaults = {
        "id": "t-1",
        "kind": "RESTART_APP",
        "status": "PENDING_CONFIRM",
        "payload": {},
        "confirm_token": "tok-1",
        "confirm_expires_at": datetime.now(UTC) + timedelta(minutes=5),
        "requested_by_open_id": "ou_owner",
    }
    defaults.update(overrides)
    return OpsTask(**defaults)


class TestBuildRestartPlan(unittest.IsolatedAsyncioTestCase):
    async def test_import_active_rejected(self):
        with patch("ops_bot.release.flow.OpsAsyncSessionFactory", _fake_session_ctx), patch(
            "ops_bot.release.flow.import_status.any_active", new=AsyncMock(return_value=True)
        ):
            plan, reason = await flow.build_restart_plan("ou_owner")

        self.assertIsNone(plan)
        self.assertIn("导入进行中", reason)

    async def test_idle_creates_pending_confirm_task(self):
        with patch("ops_bot.release.flow.OpsAsyncSessionFactory", _fake_session_ctx), patch(
            "ops_bot.release.flow.import_status.any_active", new=AsyncMock(return_value=False)
        ), patch(
            "ops_bot.release.flow.crud.create_pending_confirm_task",
            new=AsyncMock(return_value=("t-1", "tok-1")),
        ):
            plan, reason = await flow.build_restart_plan("ou_owner")

        self.assertIsNone(reason)
        self.assertEqual(plan.task_id, "t-1")


class TestBuildDeployPlan(unittest.IsolatedAsyncioTestCase):
    def _patches(self, *, sha_ok=True, ancestor=True, ci_pass=True, rollback=False, migration=True, import_active=False):
        from ops_bot.release.ci import CICheckResult

        return [
            patch("ops_bot.release.flow.git_ref.is_valid_sha_format", return_value=sha_ok),
            patch("ops_bot.release.flow.git_ref.is_ancestor_of_branch", return_value=ancestor),
            patch(
                "ops_bot.release.flow.ci.check",
                new=AsyncMock(return_value=CICheckResult(passing=ci_pass, details={"backend": "成功"})),
            ),
            patch("ops_bot.release.flow.git_ref.rollback_needs_review", return_value=rollback),
            patch("ops_bot.release.flow.git_ref.migrations_changed", return_value=migration),
            patch("ops_bot.release.flow.OpsAsyncSessionFactory", _fake_session_ctx),
            patch("ops_bot.release.flow.import_status.any_active", new=AsyncMock(return_value=import_active)),
            patch(
                "ops_bot.release.flow.crud.create_pending_confirm_task",
                new=AsyncMock(return_value=("t-1", "tok-1")),
            ),
        ]

    async def test_invalid_sha_format_rejected(self):
        patches = self._patches(sha_ok=False)
        with patches[0]:
            plan, reason = await flow.build_deploy_plan("short", "ou_owner")

        self.assertIsNone(plan)
        self.assertIn("40 位", reason)

    async def test_not_ancestor_rejected(self):
        patches = self._patches(ancestor=False)
        with patches[0], patches[1]:
            plan, reason = await flow.build_deploy_plan("a" * 40, "ou_owner")

        self.assertIsNone(plan)
        self.assertIn("允许分支", reason)

    async def test_ancestor_unknown_rejected_not_treated_as_pass(self):
        patches = self._patches(ancestor=None)
        with patches[0], patches[1]:
            plan, reason = await flow.build_deploy_plan("a" * 40, "ou_owner")

        self.assertIsNone(plan)

    async def test_ci_failing_rejected(self):
        patches = self._patches(ci_pass=False)
        with patches[0], patches[1], patches[2]:
            plan, reason = await flow.build_deploy_plan("a" * 40, "ou_owner")

        self.assertIsNone(plan)
        self.assertIn("CI", reason)

    async def test_rollback_needs_review_rejected(self):
        patches = self._patches(rollback=True)
        with patches[0], patches[1], patches[2], patches[3]:
            plan, reason = await flow.build_deploy_plan("a" * 40, "ou_owner")

        self.assertIsNone(plan)
        self.assertIn("不支持自动降级", reason)

    async def test_rollback_unknown_rejected(self):
        patches = self._patches(rollback=None)
        with patches[0], patches[1], patches[2], patches[3]:
            plan, reason = await flow.build_deploy_plan("a" * 40, "ou_owner")

        self.assertIsNone(plan)

    async def test_import_active_rejected(self):
        patches = self._patches(import_active=True)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            plan, reason = await flow.build_deploy_plan("a" * 40, "ou_owner")

        self.assertIsNone(plan)
        self.assertIn("导入进行中", reason)

    async def test_happy_path_creates_task_with_migration_flag(self):
        patches = self._patches(migration=True)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7] as mock_create:
            plan, reason = await flow.build_deploy_plan("a" * 40, "ou_owner")

        self.assertIsNone(reason)
        self.assertEqual(plan.task_id, "t-1")
        payload = mock_create.call_args.kwargs["payload"]
        self.assertTrue(payload["with_migration"])
        self.assertEqual(payload["target_sha"], "a" * 40)


class TestHandleConfirmCallback(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_task_rejected(self):
        with patch("ops_bot.release.flow.OpsAsyncSessionFactory", _fake_session_ctx), patch(
            "ops_bot.release.flow.crud.get_task", new=AsyncMock(return_value=None)
        ):
            reply = await flow.handle_confirm_callback("t-1", "tok-1", "confirm")

        self.assertIn("已失效", reply)

    async def test_wrong_token_rejected(self):
        task = _task(confirm_token="different")
        with patch("ops_bot.release.flow.OpsAsyncSessionFactory", _fake_session_ctx), patch(
            "ops_bot.release.flow.crud.get_task", new=AsyncMock(return_value=task)
        ):
            reply = await flow.handle_confirm_callback("t-1", "tok-1", "confirm")

        self.assertIn("已失效", reply)

    async def test_already_confirmed_task_rejected(self):
        task = _task(status="CONFIRMED")
        with patch("ops_bot.release.flow.OpsAsyncSessionFactory", _fake_session_ctx), patch(
            "ops_bot.release.flow.crud.get_task", new=AsyncMock(return_value=task)
        ):
            reply = await flow.handle_confirm_callback("t-1", "tok-1", "confirm")

        self.assertIn("已处理或已过期", reply)

    async def test_expired_task_marked_and_rejected(self):
        task = _task(confirm_expires_at=datetime.now(UTC) - timedelta(minutes=1))
        mark_expired = AsyncMock()
        with patch("ops_bot.release.flow.OpsAsyncSessionFactory", _fake_session_ctx), patch(
            "ops_bot.release.flow.crud.get_task", new=AsyncMock(return_value=task)
        ), patch("ops_bot.release.flow.crud.mark_task_expired", mark_expired):
            reply = await flow.handle_confirm_callback("t-1", "tok-1", "confirm")

        mark_expired.assert_awaited_once()
        self.assertIn("已过期", reply)

    async def test_cancel_marks_failed(self):
        task = _task()
        mark_failed = AsyncMock()
        with patch("ops_bot.release.flow.OpsAsyncSessionFactory", _fake_session_ctx), patch(
            "ops_bot.release.flow.crud.get_task", new=AsyncMock(return_value=task)
        ), patch("ops_bot.release.flow.crud.mark_task_failed", mark_failed):
            reply = await flow.handle_confirm_callback("t-1", "tok-1", "cancel")

        mark_failed.assert_awaited_once()
        self.assertIn("已取消", reply)

    async def test_concurrent_confirm_loses_race(self):
        """乐观锁：try_confirm_task 返回 False 时不能继续执行（双击/重放兜底）。"""
        task = _task()
        with patch("ops_bot.release.flow.OpsAsyncSessionFactory", _fake_session_ctx), patch(
            "ops_bot.release.flow.crud.get_task", new=AsyncMock(return_value=task)
        ), patch("ops_bot.release.flow.crud.try_confirm_task", new=AsyncMock(return_value=False)), patch(
            "ops_bot.release.flow._execute_confirmed"
        ) as mock_execute:
            reply = await flow.handle_confirm_callback("t-1", "tok-1", "confirm")

        mock_execute.assert_not_called()
        self.assertIn("已处理或已过期", reply)

    async def test_confirmed_proceeds_to_execute(self):
        task = _task()
        with patch("ops_bot.release.flow.OpsAsyncSessionFactory", _fake_session_ctx), patch(
            "ops_bot.release.flow.crud.get_task", new=AsyncMock(return_value=task)
        ), patch("ops_bot.release.flow.crud.try_confirm_task", new=AsyncMock(return_value=True)), patch(
            "ops_bot.release.flow._execute_confirmed", new=AsyncMock(return_value="✅ 重启应用成功")
        ) as mock_execute:
            reply = await flow.handle_confirm_callback("t-1", "tok-1", "confirm")

        # confirmed_at 是调用瞬间生成的 datetime，不做精确匹配，只核对前三个
        # 位置参数（task_id/kind/payload）与传了 actor_open_id。
        call_args = mock_execute.call_args.args
        self.assertEqual(call_args[0], "t-1")
        self.assertEqual(call_args[1], "RESTART_APP")
        self.assertEqual(call_args[2], {})
        self.assertEqual(call_args[3], "ou_owner")
        self.assertIn("成功", reply)


class TestExecuteConfirmed(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _now():
        return datetime.now(UTC)

    async def test_import_active_at_execute_time_aborts(self):
        with patch("ops_bot.release.flow.OpsAsyncSessionFactory", _fake_session_ctx), patch(
            "ops_bot.release.flow.import_status.any_active", new=AsyncMock(return_value=True)
        ), patch("ops_bot.release.flow.crud.mark_task_failed", new=AsyncMock()), patch(
            "ops_bot.release.flow.crud.write_audit_log", new=AsyncMock()
        ):
            reply = await flow._execute_confirmed("t-1", "RESTART_APP", {}, "ou_owner", self._now())

        self.assertIn("导入进行中", reply)

    async def test_lock_not_acquired_aborts(self):
        """抢不到运维动作锁只有一种成因：另一个重启/发布正握着它——这是互斥
        （design.md D5「重启与发布互斥」）落地的地方，错误提示不能说成"导入进行中"，
        那是另一层完全不同的判断。
        """
        with patch("ops_bot.release.flow.OpsAsyncSessionFactory", _fake_session_ctx), patch(
            "ops_bot.release.flow.import_status.any_active", new=AsyncMock(return_value=False)
        ), patch("ops_bot.release.flow.crud.mark_task_running", new=AsyncMock()), patch(
            "ops_bot.release.flow.crud.mark_task_failed", new=AsyncMock()
        ), patch("ops_bot.release.flow.crud.write_audit_log", new=AsyncMock()), patch(
            "ops_bot.release.flow.lock.try_acquire", lambda: _lock_ctx(False)
        ):
            reply = await flow._execute_confirmed("t-1", "RESTART_APP", {}, "ou_owner", self._now())

        self.assertIn("另一个重启/发布任务正在执行中", reply)

    async def test_restart_success_calls_executor_and_marks_succeeded(self):
        mark_succeeded = AsyncMock()
        write_audit = AsyncMock()
        with patch("ops_bot.release.flow.OpsAsyncSessionFactory", _fake_session_ctx), patch(
            "ops_bot.release.flow.import_status.any_active", new=AsyncMock(return_value=False)
        ), patch("ops_bot.release.flow.crud.mark_task_running", new=AsyncMock()), patch(
            "ops_bot.release.flow.crud.mark_task_succeeded", mark_succeeded
        ), patch("ops_bot.release.flow.crud.write_audit_log", write_audit), patch(
            "ops_bot.release.flow.lock.try_acquire", lambda: _lock_ctx(True)
        ), patch("ops_bot.release.flow._open_maintenance", new=AsyncMock()), patch(
            "ops_bot.release.flow.executor.restart_app",
            return_value=ExecutionResult(True, "ok", None),
        ):
            reply = await flow._execute_confirmed("t-1", "RESTART_APP", {}, "ou_owner", self._now())

        mark_succeeded.assert_awaited_once()
        write_audit.assert_awaited_once()
        self.assertEqual(write_audit.call_args.kwargs["result"], "SUCCEEDED")
        self.assertEqual(write_audit.call_args.kwargs["actor_open_id"], "ou_owner")
        self.assertIn("✅", reply)

    async def test_deploy_reverifies_ci_before_executing(self):
        """执行前重新核实 CI——不信任确认时刻的快照。"""
        from ops_bot.release.ci import CICheckResult

        with patch("ops_bot.release.flow.OpsAsyncSessionFactory", _fake_session_ctx), patch(
            "ops_bot.release.flow.import_status.any_active", new=AsyncMock(return_value=False)
        ), patch("ops_bot.release.flow.crud.mark_task_running", new=AsyncMock()), patch(
            "ops_bot.release.flow.crud.mark_task_failed", new=AsyncMock()
        ), patch("ops_bot.release.flow.crud.write_audit_log", new=AsyncMock()), patch(
            "ops_bot.release.flow.ci.check",
            new=AsyncMock(return_value=CICheckResult(passing=False, details={})),
        ) as mock_ci:
            reply = await flow._execute_confirmed(
                "t-1", "DEPLOY", {"target_sha": "a" * 40, "with_migration": False}, "ou_owner", self._now()
            )

        mock_ci.assert_awaited_once()
        self.assertIn("CI 未通过", reply)

    async def test_deploy_success_passes_correct_plan_to_executor(self):
        from ops_bot.release.ci import CICheckResult

        captured = {}

        def fake_deploy(plan):
            captured["plan"] = plan
            return ExecutionResult(True, "deployed", None)

        with patch("ops_bot.release.flow.OpsAsyncSessionFactory", _fake_session_ctx), patch(
            "ops_bot.release.flow.import_status.any_active", new=AsyncMock(return_value=False)
        ), patch("ops_bot.release.flow.crud.mark_task_running", new=AsyncMock()), patch(
            "ops_bot.release.flow.crud.mark_task_succeeded", new=AsyncMock()
        ), patch("ops_bot.release.flow.crud.write_audit_log", new=AsyncMock()), patch(
            "ops_bot.release.flow.ci.check",
            new=AsyncMock(return_value=CICheckResult(passing=True, details={})),
        ), patch(
            "ops_bot.release.flow.git_ref.is_ancestor_of_branch", return_value=True
        ), patch("ops_bot.release.flow.lock.try_acquire", lambda: _lock_ctx(True)), patch(
            "ops_bot.release.flow._open_maintenance", new=AsyncMock()
        ), patch("ops_bot.release.flow.executor.deploy", side_effect=fake_deploy):
            reply = await flow._execute_confirmed(
                "t-1", "DEPLOY", {"target_sha": "a" * 40, "with_migration": True}, "ou_owner", self._now()
            )

        self.assertEqual(captured["plan"].target_sha, "a" * 40)
        self.assertTrue(captured["plan"].with_migration)
        self.assertIn("✅", reply)


if __name__ == "__main__":
    unittest.main()
