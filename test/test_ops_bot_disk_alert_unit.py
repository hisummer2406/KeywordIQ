"""磁盘告警分档：70～90% 每天提醒一次且不升级，90% 起按常规静默期频繁告警。"""

from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from config import settings
from ops_bot.checks import resources
from ops_bot.feishu.client import FeishuClient
from ops_bot.jobs import build_jobs


@asynccontextmanager
async def _session():
    yield AsyncMock()


class TestDiskAlertBands(unittest.IsolatedAsyncioTestCase):
    async def _evaluate_at(self, percent: float) -> dict:
        jobs = build_jobs(FeishuClient("id", "secret"), "oc_ops")
        job = next(j for j in jobs if j.kind == "DISK_CHECK")
        with patch("ops_bot.jobs.OpsAsyncSessionFactory", _session), patch(
            "ops_bot.jobs.resources.disk_usage_percent", return_value=percent
        ), patch("ops_bot.jobs.alerts.evaluate", new=AsyncMock()) as evaluate:
            await job.run()
        return evaluate.call_args.kwargs

    async def test_below_warn_is_not_alerting(self):
        self.assertFalse((await self._evaluate_at(69.9))["is_alerting"])

    async def test_warn_band_reminds_daily_without_escalation(self):
        for percent in (70.0, 80.0, 89.9):
            kwargs = await self._evaluate_at(percent)
            self.assertTrue(kwargs["is_alerting"])
            self.assertEqual(kwargs["level"], "warn")
            self.assertEqual(kwargs["silence_minutes"], resources.DISK_WARN_REMINDER_MINUTES)
            self.assertEqual(kwargs["escalate_after"], 0)

    async def test_critical_band_uses_normal_silence_and_escalation(self):
        kwargs = await self._evaluate_at(90.0)
        self.assertTrue(kwargs["is_alerting"])
        self.assertEqual(kwargs["level"], "crit")
        self.assertEqual(kwargs["silence_minutes"], settings.OPS_ALERT_SILENCE_MINUTES)
        self.assertNotIn("escalate_after", kwargs)


if __name__ == "__main__":
    unittest.main()
