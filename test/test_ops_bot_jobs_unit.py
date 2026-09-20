"""`jobs.build_jobs()` 里各闭包的单元测试：重点覆盖"通知失败与执行失败分开记录"
（design.md「通知失败与执行失败分开记录」）——这里曾经漏了，单条通知发送失败会让
整个 for 循环中途抛出，连带丢失同一轮已经算好但还没提交的基线记录。
"""

from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from ops_bot.feishu.client import FeishuAPIError, FeishuClient
from ops_bot.jobs import build_jobs


@asynccontextmanager
async def _fake_session_ctx():
    yield AsyncMock()


def _find_job(jobs, kind):
    return next(j for j in jobs if j.kind == kind)


class TestImportNotify(unittest.IsolatedAsyncioTestCase):
    def _jobs(self):
        client = FeishuClient("id", "secret")
        return build_jobs(client, "oc_ops")

    async def test_send_failure_does_not_abort_remaining_batches_or_baseline(self):
        batches = [
            {
                "id": 1, "site": "US", "period_type": "DAY", "report_date": "2026-09-16",
                "status": "COMPLETED", "processing_seconds": 120, "error_message": "",
            },
            {
                "id": 2, "site": "GB", "period_type": "DAY", "report_date": "2026-09-16",
                "status": "COMPLETED", "processing_seconds": 90, "error_message": "",
            },
        ]
        record_baseline = AsyncMock()
        commit_calls = []

        session = AsyncMock()
        session.commit = AsyncMock(side_effect=lambda: commit_calls.append(1))

        @asynccontextmanager
        async def _session_ctx():
            yield session

        job = _find_job(self._jobs(), "IMPORT_NOTIFY")

        with patch("ops_bot.jobs.OpsAsyncSessionFactory", _session_ctx), patch(
            "ops_bot.jobs.crud.last_import_notify_watermark", new=AsyncMock(return_value=None)
        ), patch(
            "ops_bot.jobs.import_status.find_new_terminal_batches", new=AsyncMock(return_value=batches)
        ), patch("ops_bot.jobs.import_status.record_baseline", record_baseline), patch(
            "ops_bot.jobs.FeishuClient.send_text",
            new=AsyncMock(side_effect=[FeishuAPIError("boom"), None]),
        ):
            output = await job.run()

        # 两条批次的基线都要记到——第一条的发送失败不能连累第二条，也不能让
        # 已经 record_baseline 过的第一条因为 commit 从未执行到而白算。
        self.assertEqual(record_baseline.await_count, 2)
        self.assertEqual(len(commit_calls), 1)
        self.assertIn("1 条发送失败", output)
        # 有发送失败就不能把水位线推进到本轮——维持在上一轮的 None，编码成空
        # 标记（PR #99 复审第三轮 P2）。
        self.assertTrue(output.endswith("\nwatermark="))

    async def test_send_failure_does_not_advance_watermark(self):
        """有任一条发送失败时，水位线必须原样保留上一轮的值，不能推进到本轮
        cutoff——否则下一轮查询会把发送失败的那条永久排除在外
        （PR #99 复审第三轮 P2）。
        """
        batches = [
            {
                "id": 1, "site": "US", "period_type": "DAY", "report_date": "2026-09-16",
                "status": "COMPLETED", "processing_seconds": 120, "error_message": "",
            },
        ]

        @asynccontextmanager
        async def _session_ctx():
            yield AsyncMock()

        job = _find_job(self._jobs(), "IMPORT_NOTIFY")
        previous_watermark = datetime(2026, 9, 16, 3, 0, tzinfo=UTC)

        with patch("ops_bot.jobs.OpsAsyncSessionFactory", _session_ctx), patch(
            "ops_bot.jobs.crud.last_import_notify_watermark",
            new=AsyncMock(return_value=previous_watermark),
        ), patch(
            "ops_bot.jobs.import_status.find_new_terminal_batches", new=AsyncMock(return_value=batches)
        ), patch("ops_bot.jobs.import_status.record_baseline", new=AsyncMock()), patch(
            "ops_bot.jobs.FeishuClient.send_text", new=AsyncMock(side_effect=FeishuAPIError("boom"))
        ):
            output = await job.run()

        self.assertIn(f"watermark={previous_watermark.isoformat()}", output)

    async def test_success_advances_watermark_to_fresh_cutoff(self):
        """全部发送成功时，水位线必须推进到本轮查询前捕获的 cutoff——不是任务的
        `created_at`（PR #99 复审第三轮 P2，取代此前锁定 `created_at` 的用例）。
        """

        @asynccontextmanager
        async def _session_ctx():
            yield AsyncMock()

        job = _find_job(self._jobs(), "IMPORT_NOTIFY")
        before = datetime.now(UTC)

        with patch("ops_bot.jobs.OpsAsyncSessionFactory", _session_ctx), patch(
            "ops_bot.jobs.crud.last_import_notify_watermark", new=AsyncMock(return_value=None)
        ), patch("ops_bot.jobs.import_status.find_new_terminal_batches", new=AsyncMock(return_value=[])):
            output = await job.run()

        after = datetime.now(UTC)
        marker_line = next(line for line in output.splitlines() if line.startswith("watermark="))
        watermark = datetime.fromisoformat(marker_line.removeprefix("watermark="))
        self.assertTrue(before <= watermark <= after)

    async def test_no_failures_reports_plain_count(self):
        batches = [
            {
                "id": 1, "site": "US", "period_type": "DAY", "report_date": "2026-09-16",
                "status": "FAILED", "processing_seconds": 0, "error_message": "boom",
            },
        ]

        @asynccontextmanager
        async def _session_ctx():
            yield AsyncMock()

        job = _find_job(self._jobs(), "IMPORT_NOTIFY")

        with patch("ops_bot.jobs.OpsAsyncSessionFactory", _session_ctx), patch(
            "ops_bot.jobs.crud.last_import_notify_watermark", new=AsyncMock(return_value=None)
        ), patch(
            "ops_bot.jobs.import_status.find_new_terminal_batches", new=AsyncMock(return_value=batches)
        ), patch("ops_bot.jobs.FeishuClient.send_text", new=AsyncMock(return_value=None)):
            output = await job.run()

        self.assertTrue(output.startswith("1 条新完成/失败通知\nwatermark="))

    async def test_uses_import_notify_watermark_not_finished_at(self):
        """高水位线必须来自 `last_import_notify_watermark`，不是 `last_succeeded_at`
        （任务完成时间）——后者会漏记"本轮查询已执行、本轮任务尚未标记完成"这段
        窗口内转终态的批次（PR #99 复审第二轮 P2，机制在第三轮进一步修正，
        见 crud.last_import_notify_watermark 文档字符串）。
        """

        @asynccontextmanager
        async def _session_ctx():
            yield AsyncMock()

        job = _find_job(self._jobs(), "IMPORT_NOTIFY")

        with patch("ops_bot.jobs.OpsAsyncSessionFactory", _session_ctx), patch(
            "ops_bot.jobs.crud.last_import_notify_watermark", new=AsyncMock(return_value=None)
        ) as mock_watermark, patch(
            "ops_bot.jobs.crud.last_succeeded_at", new=AsyncMock()
        ) as mock_stale_watermark, patch(
            "ops_bot.jobs.import_status.find_new_terminal_batches", new=AsyncMock(return_value=[])
        ):
            await job.run()

        mock_watermark.assert_awaited_once()
        mock_stale_watermark.assert_not_awaited()


class TestNoBackupJobs(unittest.TestCase):
    """不做定时备份：不注册备份任务，也不注册「备份逾期」告警——后者在从未备份过时
    每一轮巡检都会告警（手动的「备份」指令仍在，见 commands / main）。"""

    def test_no_backup_or_backup_overdue_job_registered(self):
        kinds = {job.kind for job in build_jobs(FeishuClient("id", "secret"), "oc_ops")}

        self.assertNotIn("BACKUP", kinds)
        self.assertNotIn("BACKUP_OVERDUE_CHECK", kinds)


class TestPoolCheck(unittest.IsolatedAsyncioTestCase):
    """`pool_water_level()` 探测失败返回 `None`，不代表"水位为 0"——见该函数
    的文档约定。`is_alerting` 必须把它当成异常，不能当成健康（PR #99 Codex
    复审 P2：旧实现 `ratio is not None and ...` 会让探测失败被判定为不告警）。
    """

    def _jobs(self):
        client = FeishuClient("id", "secret")
        return build_jobs(client, "oc_ops")

    async def test_probe_failure_is_treated_as_alerting_not_healthy(self):
        job = _find_job(self._jobs(), "POOL_CHECK")

        with patch("ops_bot.jobs.OpsAsyncSessionFactory", _fake_session_ctx), patch(
            "ops_bot.jobs.service.pool_water_level", new=AsyncMock(return_value=None)
        ), patch("ops_bot.jobs.alerts.evaluate", new=AsyncMock()) as mock_evaluate:
            output = await job.run()

        self.assertEqual(mock_evaluate.call_args.kwargs["is_alerting"], True)
        self.assertEqual(output, "unknown")

    async def test_high_water_ratio_is_alerting(self):
        job = _find_job(self._jobs(), "POOL_CHECK")

        with patch("ops_bot.jobs.OpsAsyncSessionFactory", _fake_session_ctx), patch(
            "ops_bot.jobs.service.pool_water_level", new=AsyncMock(return_value=0.95)
        ), patch("ops_bot.jobs.alerts.evaluate", new=AsyncMock()) as mock_evaluate:
            output = await job.run()

        self.assertEqual(mock_evaluate.call_args.kwargs["is_alerting"], True)
        self.assertEqual(output, "95%")

    async def test_low_water_ratio_is_not_alerting(self):
        job = _find_job(self._jobs(), "POOL_CHECK")

        with patch("ops_bot.jobs.OpsAsyncSessionFactory", _fake_session_ctx), patch(
            "ops_bot.jobs.service.pool_water_level", new=AsyncMock(return_value=0.1)
        ), patch("ops_bot.jobs.alerts.evaluate", new=AsyncMock()) as mock_evaluate:
            output = await job.run()

        self.assertEqual(mock_evaluate.call_args.kwargs["is_alerting"], False)
        self.assertEqual(output, "10%")


class TestAlembicDriftCheck(unittest.IsolatedAsyncioTestCase):
    """`alembic_drift.is_in_sync()` 探测失败返回 `None`，与该函数文档字符串同一条
    纪律：`None` 是"无法核实"，不是"已同步"，必须按异常处理，不能被判定为不告警
    （PR #99 复审第二轮 P2）。"""

    def _jobs(self):
        client = FeishuClient("id", "secret")
        return build_jobs(client, "oc_ops")

    async def test_probe_failure_is_treated_as_alerting_not_synced(self):
        job = _find_job(self._jobs(), "ALEMBIC_DRIFT_CHECK")

        with patch("ops_bot.jobs.OpsAsyncSessionFactory", _fake_session_ctx), patch(
            "ops_bot.jobs.alembic_drift.is_in_sync", return_value=None
        ), patch("ops_bot.jobs.alerts.evaluate", new=AsyncMock()) as mock_evaluate:
            output = await job.run()

        self.assertEqual(mock_evaluate.call_args.kwargs["is_alerting"], True)
        self.assertEqual(output, "探测失败")

    async def test_drift_detected_is_alerting(self):
        job = _find_job(self._jobs(), "ALEMBIC_DRIFT_CHECK")

        with patch("ops_bot.jobs.OpsAsyncSessionFactory", _fake_session_ctx), patch(
            "ops_bot.jobs.alembic_drift.is_in_sync", return_value=False
        ), patch("ops_bot.jobs.alerts.evaluate", new=AsyncMock()) as mock_evaluate:
            output = await job.run()

        self.assertEqual(mock_evaluate.call_args.kwargs["is_alerting"], True)
        self.assertEqual(output, "漂移")

    async def test_in_sync_is_not_alerting(self):
        job = _find_job(self._jobs(), "ALEMBIC_DRIFT_CHECK")

        with patch("ops_bot.jobs.OpsAsyncSessionFactory", _fake_session_ctx), patch(
            "ops_bot.jobs.alembic_drift.is_in_sync", return_value=True
        ), patch("ops_bot.jobs.alerts.evaluate", new=AsyncMock()) as mock_evaluate:
            output = await job.run()

        self.assertEqual(mock_evaluate.call_args.kwargs["is_alerting"], False)
        self.assertEqual(output, "同步")

    async def test_probe_runs_off_the_event_loop(self):
        """两个各 60 秒超时的 `docker compose run` 子进程必须丢进线程池，不能直接
        同步跑在事件循环上——否则探测卡住时 `/ops/health`、飞书回调都会连带
        无响应（PR #99 复审第二轮 P2）。"""
        job = _find_job(self._jobs(), "ALEMBIC_DRIFT_CHECK")

        with patch("ops_bot.jobs.OpsAsyncSessionFactory", _fake_session_ctx), patch(
            "ops_bot.jobs.alerts.evaluate", new=AsyncMock()
        ), patch("ops_bot.jobs.asyncio.to_thread", new=AsyncMock(return_value=True)) as mock_to_thread, patch(
            "ops_bot.jobs.alembic_drift.is_in_sync"
        ) as mock_is_in_sync:
            await job.run()

        mock_to_thread.assert_awaited_once_with(mock_is_in_sync)


if __name__ == "__main__":
    unittest.main()
