"""定时保留清理：运行模式、告警，以及失败时绝不抛给调度器（否则每 60 秒重跑）。"""

from __future__ import annotations

import subprocess
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from ops_bot import retention
from ops_bot.feishu.client import FeishuClient
from ops_bot.jobs import build_jobs

_OK = {"status": "ok", "dropped": {"fact_daily": ["fact_daily_2026_09"]}, "purged_stats": {}, "blocked": [], "blocked_months": []}
_BLOCKED = {
    "status": "ok", "dropped": {}, "purged_stats": {}, "blocked_months": ["2026-09"],
    "blocked": [
        {"month": "2026-09", "site": "US", "period_type": "DAY", "report_date": f"2026-09-{d:02d}", "reason": "object_missing"}
        for d in range(1, 26)
    ],
}


@asynccontextmanager
async def _session():
    yield AsyncMock()


def _jobs(mode):
    with patch("ops_bot.jobs.settings.OPS_RETENTION_MODE", mode):
        return build_jobs(FeishuClient("id", "secret"), "oc_ops")


def _job(mode):
    return next(j for j in _jobs(mode) if j.kind == "RETENTION_PRUNE")


class TestRegistration(unittest.TestCase):
    def test_off_does_not_register(self):
        self.assertFalse([j for j in _jobs("off") if j.kind == "RETENTION_PRUNE"])

    def test_dry_run_and_execute_register(self):
        _job("dry_run")
        _job("execute")


class TestJob(unittest.IsolatedAsyncioTestCase):
    async def _run(self, mode, side_effect):
        job = _job(mode)
        with patch("ops_bot.jobs.settings.OPS_RETENTION_MODE", mode), patch(
            "ops_bot.jobs.OpsAsyncSessionFactory", _session
        ), patch("ops_bot.jobs.alerts.evaluate", new=AsyncMock()) as evaluate, patch(
            "ops_bot.jobs.retention.run_prune", side_effect=side_effect
        ) as prune:
            output = await job.run()
        alerts = {c.kwargs["alert_key"]: c.kwargs for c in evaluate.await_args_list}
        return output, alerts, prune

    async def test_dry_run_mode_never_executes(self):
        _, _, prune = await self._run("dry_run", [_OK])
        self.assertEqual([c.kwargs["dry_run"] for c in prune.call_args_list], [True])

    async def test_execute_previews_first(self):
        output, _, prune = await self._run("execute", [_OK, _OK])
        self.assertEqual([c.kwargs["dry_run"] for c in prune.call_args_list], [True, False])
        self.assertIn("预览", output)
        self.assertIn("执行", output)

    async def test_blocked_alerts_with_capped_detail(self):
        _, alerts, _ = await self._run("dry_run", [_BLOCKED])
        gate = alerts["retention-gate-blocked"]
        self.assertTrue(gate["is_alerting"])
        self.assertIn("另有 5 条", gate["message"])
        self.assertFalse(alerts["retention-job-failed"]["is_alerting"])

    async def test_failure_alerts_and_does_not_raise(self):
        output, alerts, prune = await self._run("execute", retention.RetentionRunError("清理进程退出码 1"))
        self.assertTrue(alerts["retention-job-failed"]["is_alerting"])
        self.assertNotIn("retention-gate-blocked", alerts)
        self.assertEqual(prune.call_count, 1)
        self.assertIn("失败", output)

    async def test_execute_failure_after_preview_keeps_preview_trace(self):
        output, alerts, _ = await self._run("execute", [_OK, retention.RetentionRunError("超时")])
        self.assertIn("预览", output)
        self.assertTrue(alerts["retention-job-failed"]["is_alerting"])

    async def test_lock_skipped_is_not_failure_and_keeps_gate_state(self):
        output, alerts, _ = await self._run("dry_run", [{"status": "lock_skipped"}])
        self.assertIn("跳过", output)
        self.assertFalse(alerts["retention-job-failed"]["is_alerting"])
        self.assertNotIn("retention-gate-blocked", alerts)

    async def test_alert_delivery_error_does_not_raise(self):
        job = _job("dry_run")
        with patch("ops_bot.jobs.settings.OPS_RETENTION_MODE", "dry_run"), patch(
            "ops_bot.jobs.OpsAsyncSessionFactory", _session
        ), patch("ops_bot.jobs.alerts.evaluate", new=AsyncMock(side_effect=RuntimeError("db"))), patch(
            "ops_bot.jobs.retention.run_prune", return_value=_OK
        ):
            output = await job.run()
        self.assertIn("告警发送失败", output)


class TestRunPrune(unittest.TestCase):
    def _proc(self, code=0, stdout="", stderr=""):
        return MagicMock(returncode=code, stdout=stdout, stderr=stderr)

    def test_uses_last_line_and_app_image(self):
        with patch("ops_bot.retention.subprocess.run", return_value=self._proc(stdout='noise\n{"status":"ok"}\n')) as run:
            self.assertEqual(retention.run_prune(dry_run=True), {"status": "ok"})
        self.assertEqual(run.call_args.args[0][:5], ["docker", "compose", "run", "--rm", "app"])
        self.assertIn("--dry-run", run.call_args.args[0])

    def test_execute_passes_yes(self):
        with patch("ops_bot.retention.subprocess.run", return_value=self._proc(stdout='{"status":"ok"}')) as run:
            retention.run_prune(dry_run=False)
        self.assertIn("--yes", run.call_args.args[0])
        self.assertNotIn("--dry-run", run.call_args.args[0])

    def test_failures_raise_without_leaking_output(self):
        for proc in (self._proc(code=1, stderr="secret"), self._proc(stdout="not json"), self._proc(stdout=""), self._proc(stdout='{"status":"weird"}')):
            with patch("ops_bot.retention.subprocess.run", return_value=proc):
                with self.assertRaises(retention.RetentionRunError) as ctx:
                    retention.run_prune(dry_run=True)
                self.assertNotIn("secret", str(ctx.exception))

    def test_timeout_raises(self):
        with patch("ops_bot.retention.subprocess.run", side_effect=subprocess.TimeoutExpired("x", 1)):
            with self.assertRaises(retention.RetentionRunError):
                retention.run_prune(dry_run=True)


if __name__ == "__main__":
    unittest.main()
