"""调度循环的单元测试：全部打桩数据库层，只验证控制流（幂等判据、成功/失败记录、取消）。"""

from __future__ import annotations

import asyncio
import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from ops_bot.scheduler import ScheduledJob, _is_due, _run_once, start, stop


@asynccontextmanager
async def _fake_session_ctx():
    yield AsyncMock()


class TestIsDue(unittest.IsolatedAsyncioTestCase):
    async def test_never_run_is_due(self):
        with patch("ops_bot.scheduler.OpsAsyncSessionFactory", _fake_session_ctx), patch(
            "ops_bot.scheduler.crud.last_succeeded_at", new=AsyncMock(return_value=None)
        ):
            self.assertTrue(await _is_due("X", 60))

    async def test_recent_success_not_due(self):
        recent = datetime.now(UTC) - timedelta(seconds=5)
        with patch("ops_bot.scheduler.OpsAsyncSessionFactory", _fake_session_ctx), patch(
            "ops_bot.scheduler.crud.last_succeeded_at", new=AsyncMock(return_value=recent)
        ):
            self.assertFalse(await _is_due("X", 60))

    async def test_stale_success_is_due(self):
        """服务重启不会因为内存计时器清零而漏跑——判据是数据库里的时间戳，不是进程状态。"""
        old = datetime.now(UTC) - timedelta(seconds=120)
        with patch("ops_bot.scheduler.OpsAsyncSessionFactory", _fake_session_ctx), patch(
            "ops_bot.scheduler.crud.last_succeeded_at", new=AsyncMock(return_value=old)
        ):
            self.assertTrue(await _is_due("X", 60))


class TestRunOnce(unittest.IsolatedAsyncioTestCase):
    async def test_success_records_succeeded(self):
        mark_succeeded = AsyncMock()
        mark_failed = AsyncMock()
        job = ScheduledJob(kind="X", interval_seconds=60, run=AsyncMock(return_value="ok"))

        with patch("ops_bot.scheduler.OpsAsyncSessionFactory", _fake_session_ctx), patch(
            "ops_bot.scheduler.crud.create_task", new=AsyncMock(return_value="task-1")
        ), patch("ops_bot.scheduler.crud.mark_task_succeeded", mark_succeeded), patch(
            "ops_bot.scheduler.crud.mark_task_failed", mark_failed
        ):
            await _run_once(job)

        mark_succeeded.assert_awaited_once()
        mark_failed.assert_not_awaited()

    async def test_exception_records_failed_not_raised(self):
        """任务本身抛异常不能让调度循环崩掉——记为失败，循环继续。"""
        mark_succeeded = AsyncMock()
        mark_failed = AsyncMock()

        async def _boom():
            raise RuntimeError("boom")

        job = ScheduledJob(kind="X", interval_seconds=60, run=_boom)

        with patch("ops_bot.scheduler.OpsAsyncSessionFactory", _fake_session_ctx), patch(
            "ops_bot.scheduler.crud.create_task", new=AsyncMock(return_value="task-1")
        ), patch("ops_bot.scheduler.crud.mark_task_succeeded", mark_succeeded), patch(
            "ops_bot.scheduler.crud.mark_task_failed", mark_failed
        ):
            await _run_once(job)  # 不应该抛出

        mark_failed.assert_awaited_once()
        mark_succeeded.assert_not_awaited()


class TestStartStop(unittest.IsolatedAsyncioTestCase):
    async def test_stop_cancels_all_tasks_cleanly(self):
        job = ScheduledJob(kind="X", interval_seconds=3600, run=AsyncMock(return_value="ok"))

        with patch("ops_bot.scheduler.OpsAsyncSessionFactory", _fake_session_ctx), patch(
            "ops_bot.scheduler.crud.last_succeeded_at", new=AsyncMock(return_value=datetime.now(UTC))
        ):
            tasks = start([job])
            await asyncio.sleep(0)  # 让循环跑到第一次 await
            await stop(tasks)

        self.assertTrue(all(t.done() for t in tasks))


if __name__ == "__main__":
    unittest.main()
