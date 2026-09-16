"""受限执行器的单元测试：全部打桩 `subprocess.run`，不碰真实 docker/deploy.sh。"""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from ops_bot.executor import DeployPlan, backup_now, deploy, reload_nginx, restart_app


def _completed(returncode=0, stdout="ok\n", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class TestRestartApp(unittest.TestCase):
    def test_success_uses_fixed_argv(self):
        with patch("ops_bot.executor.subprocess.run", return_value=_completed()) as mock_run:
            result = restart_app()

        self.assertTrue(result.success)
        self.assertIsNone(result.failed_stage)
        called_argv = mock_run.call_args.args[0]
        self.assertEqual(called_argv, ["docker", "compose", "restart", "app"])

    def test_nonzero_exit_reports_failure(self):
        with patch("ops_bot.executor.subprocess.run", return_value=_completed(returncode=1, stderr="boom")):
            result = restart_app()

        self.assertFalse(result.success)
        self.assertEqual(result.failed_stage, "RESTART_APP")
        self.assertIn("boom", result.output)

    def test_timeout_reports_failure_without_crashing(self):
        with patch(
            "ops_bot.executor.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=300),
        ):
            result = restart_app()

        self.assertFalse(result.success)
        self.assertEqual(result.failed_stage, "RESTART_APP")


class TestReloadNginx(unittest.TestCase):
    def test_uses_sudo_noninteractive(self):
        with patch("ops_bot.executor.subprocess.run", return_value=_completed()) as mock_run:
            reload_nginx()

        called_argv = mock_run.call_args.args[0]
        self.assertEqual(called_argv, ["sudo", "-n", "systemctl", "reload", "nginx"])


class TestBackupNow(unittest.TestCase):
    def test_calls_existing_backup_script_not_reimplemented(self):
        with patch("ops_bot.executor.subprocess.run", return_value=_completed()) as mock_run:
            result = backup_now()

        self.assertTrue(result.success)
        called_argv = mock_run.call_args.args[0]
        self.assertEqual(called_argv, ["uv", "run", "python", "scripts/backup_selfhosted_pg.py"])


class TestDeploy(unittest.TestCase):
    def test_stops_at_first_failing_step(self):
        """git checkout 失败时不应该继续跑 deploy.sh。"""
        with patch(
            "ops_bot.executor.subprocess.run",
            side_effect=[_completed(), _completed(returncode=1, stderr="unknown revision")],
        ) as mock_run:
            result = deploy(DeployPlan(target_sha="a" * 40, with_migration=False))

        self.assertFalse(result.success)
        self.assertEqual(result.failed_stage, "GIT_CHECKOUT")
        self.assertEqual(mock_run.call_count, 2)

    def test_with_migration_true_passes_no_skip_flag(self):
        with patch("ops_bot.executor.subprocess.run", return_value=_completed()) as mock_run:
            deploy(DeployPlan(target_sha="a" * 40, with_migration=True))

        deploy_call = mock_run.call_args_list[-1].args[0]
        self.assertEqual(deploy_call, ["./deploy.sh", "--yes"])

    def test_with_migration_false_passes_skip_flag(self):
        with patch("ops_bot.executor.subprocess.run", return_value=_completed()) as mock_run:
            deploy(DeployPlan(target_sha="a" * 40, with_migration=False))

        deploy_call = mock_run.call_args_list[-1].args[0]
        self.assertEqual(deploy_call, ["./deploy.sh", "--yes", "--skip-migrate"])


if __name__ == "__main__":
    unittest.main()
