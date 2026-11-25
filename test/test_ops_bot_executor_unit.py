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
        """git checkout 失败时不应该继续跑 deploy.sh，但应尝试回退到发布前的 checkout。"""
        with patch(
            "ops_bot.executor.subprocess.run",
            side_effect=[
                _completed(stdout="b" * 40 + "\n"),  # 记录发布前的 checkout
                _completed(),  # git fetch
                _completed(returncode=1, stderr="unknown revision"),  # git checkout 失败
                _completed(),  # 回退 checkout
            ],
        ) as mock_run:
            result = deploy(DeployPlan(target_sha="a" * 40, with_migration=False))

        self.assertFalse(result.success)
        self.assertEqual(result.failed_stage, "GIT_CHECKOUT")
        self.assertEqual(mock_run.call_count, 4)
        self.assertEqual(mock_run.call_args_list[-1].args[0], ["git", "checkout", "b" * 40])

    def test_with_migration_true_passes_no_skip_flag(self):
        with patch("ops_bot.executor.subprocess.run", return_value=_completed()) as mock_run:
            deploy(DeployPlan(target_sha="a" * 40, with_migration=True))

        deploy_call = mock_run.call_args_list[-1].args[0]
        self.assertEqual(deploy_call, ["env", "KEYWORDIQ_OPS_LOCK_HELD=1", "./deploy.sh", "--yes"])

    def test_with_migration_false_passes_skip_flag(self):
        with patch("ops_bot.executor.subprocess.run", return_value=_completed()) as mock_run:
            deploy(DeployPlan(target_sha="a" * 40, with_migration=False))

        deploy_call = mock_run.call_args_list[-1].args[0]
        self.assertEqual(deploy_call, ["env", "KEYWORDIQ_OPS_LOCK_HELD=1", "./deploy.sh", "--yes", "--skip-migrate"])

    def test_does_not_restore_checkout_when_deploy_sh_fails(self):
        """`deploy.sh` 失败（例如 `docker compose build` 阶段）时不能自动回退 checkout：
        `deploy.sh` 可能已经跑完迁移、切了容器，只是最后的健康检查没过才非零退出——
        执行器分不清 `DEPLOY_SH` 失败具体发生在哪个子阶段，无条件回退会让 HEAD 倒回
        旧版本、而容器与 schema 实际已经是新版本，把"当前实际部署到哪个版本"这条
        本该可信的事实来源污染成假的（PR #99 复审第四轮 P1；此前第三轮曾反过来
        无条件回退，同样是错的）。
        """
        with patch(
            "ops_bot.executor.subprocess.run",
            side_effect=[
                _completed(stdout="b" * 40 + "\n"),  # 记录发布前的 checkout
                _completed(),  # git fetch
                _completed(),  # git checkout target
                _completed(returncode=1, stderr="build failed"),  # deploy.sh 失败
            ],
        ) as mock_run:
            result = deploy(DeployPlan(target_sha="a" * 40, with_migration=True))

        self.assertFalse(result.success)
        self.assertEqual(result.failed_stage, "DEPLOY_SH")
        self.assertEqual(mock_run.call_count, 4, "记录 checkout + fetch + checkout + deploy.sh，不应再多一次回退调用")
        self.assertIn("未自动回退", result.output)

    def test_skips_restore_when_previous_sha_unknown(self):
        """记录发布前 checkout 本身失败时，不能拿 `None` 当 SHA 去尝试回退。"""
        with patch(
            "ops_bot.executor.subprocess.run",
            side_effect=[
                subprocess.CalledProcessError(1, ["git", "rev-parse", "HEAD"]),  # 记录失败
                _completed(),  # git fetch
                _completed(returncode=1, stderr="boom"),  # git checkout 失败
            ],
        ) as mock_run:
            result = deploy(DeployPlan(target_sha="a" * 40, with_migration=False))

        self.assertFalse(result.success)
        self.assertEqual(mock_run.call_count, 3)

    def test_does_not_restore_on_success(self):
        with patch("ops_bot.executor.subprocess.run", return_value=_completed()) as mock_run:
            result = deploy(DeployPlan(target_sha="a" * 40, with_migration=True))

        self.assertTrue(result.success)
        # 记录发布前 checkout（1）+ git fetch（2）+ git checkout（3）+ deploy.sh（4）
        self.assertEqual(mock_run.call_count, 4)


if __name__ == "__main__":
    unittest.main()
