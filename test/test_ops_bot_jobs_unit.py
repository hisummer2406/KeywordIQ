"""`jobs.build_jobs()` 里各闭包的单元测试：重点覆盖"通知失败与执行失败分开记录"
（design.md「通知失败与执行失败分开记录」）——这里曾经漏了，单条通知发送失败会让
整个 for 循环中途抛出，连带丢失同一轮已经算好但还没提交的基线记录。
"""

from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
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
            "ops_bot.jobs.crud.last_succeeded_at", new=AsyncMock(return_value=None)
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
            "ops_bot.jobs.crud.last_succeeded_at", new=AsyncMock(return_value=None)
        ), patch(
            "ops_bot.jobs.import_status.find_new_terminal_batches", new=AsyncMock(return_value=batches)
        ), patch("ops_bot.jobs.FeishuClient.send_text", new=AsyncMock(return_value=None)):
            output = await job.run()

        self.assertEqual(output, "1 条新完成/失败通知")


if __name__ == "__main__":
    unittest.main()
