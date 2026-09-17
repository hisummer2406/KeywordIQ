"""导入巡检的单元测试：打桩 `session.execute` 的返回结果，验证阈值判断与拼装逻辑。"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from ops_bot.checks.import_status import find_new_terminal_batches, find_stuck_batches, record_baseline


def _session_with_rows(rows: list[dict]) -> AsyncMock:
    session = AsyncMock()
    result = MagicMock()
    result.mappings.return_value = rows
    session.execute = AsyncMock(return_value=result)
    return session


class TestFindStuckBatches(unittest.IsolatedAsyncioTestCase):
    async def test_day_batch_past_threshold_is_stuck(self):
        old = datetime.now(UTC) - timedelta(minutes=90)
        session = _session_with_rows(
            [{"id": 1, "site": "US", "period_type": "DAY", "report_date": None, "status": "PROCESSING", "created_at": old}]
        )

        stuck = await find_stuck_batches(session, day_timeout_minutes=60, week_timeout_minutes=240)

        self.assertEqual(len(stuck), 1)

    async def test_day_batch_within_threshold_not_stuck(self):
        recent = datetime.now(UTC) - timedelta(minutes=10)
        session = _session_with_rows(
            [{"id": 1, "site": "US", "period_type": "DAY", "report_date": None, "status": "PROCESSING", "created_at": recent}]
        )

        stuck = await find_stuck_batches(session, day_timeout_minutes=60, week_timeout_minutes=240)

        self.assertEqual(stuck, [])

    async def test_week_batch_uses_week_threshold_not_day(self):
        """周报的阈值应该比日报宽——用日报阈值判周报会大量误报。"""
        age = datetime.now(UTC) - timedelta(minutes=90)
        session = _session_with_rows(
            [{"id": 1, "site": "US", "period_type": "WEEK", "report_date": None, "status": "PROCESSING", "created_at": age}]
        )

        stuck = await find_stuck_batches(session, day_timeout_minutes=60, week_timeout_minutes=240)

        self.assertEqual(stuck, [], "90 分钟对周报（240 分钟阈值）不该算卡死")


class TestFindNewTerminalBatches(unittest.IsolatedAsyncioTestCase):
    async def test_none_since_returns_empty_without_querying_history(self):
        """首次运行没有基准点，不该把历史上所有批次一次性当作"新"通知一遍。"""
        session = _session_with_rows([{"id": 1}])

        result = await find_new_terminal_batches(session, since=None, until=datetime.now(UTC))

        self.assertEqual(result, [])
        session.execute.assert_not_called()

    async def test_with_since_returns_rows(self):
        session = _session_with_rows(
            [{"id": 1, "period_type": "DAY", "status": "COMPLETED", "processing_seconds": 120}]
        )

        result = await find_new_terminal_batches(
            session, since=datetime.now(UTC) - timedelta(hours=1), until=datetime.now(UTC)
        )

        self.assertEqual(len(result), 1)

    async def test_query_bounds_by_until_not_open_ended(self):
        """查询必须带上界，否则 `until` 捕获之后、查询执行之前完成的批次会在
        下一轮（`since=本轮 until`）被重复扫入、重复通知（PR #99 复审第四轮 P1）。
        """
        session = _session_with_rows([])
        since = datetime.now(UTC) - timedelta(hours=1)
        until = datetime.now(UTC)

        await find_new_terminal_batches(session, since=since, until=until)

        sql_text, params = session.execute.call_args.args
        self.assertIn("completed_at <= :until", str(sql_text))
        self.assertEqual(params["until"], until)


class TestRecordBaseline(unittest.IsolatedAsyncioTestCase):
    async def test_writes_duration_from_processing_seconds(self):
        session = AsyncMock()

        await record_baseline(
            session, {"period_type": "DAY", "id": 42, "processing_seconds": 300}
        )

        session.execute.assert_awaited_once()
        params = session.execute.call_args.args[1]
        self.assertEqual(params["batch_id"], 42)
        self.assertEqual(params["duration_seconds"], 300)

    async def test_insert_is_idempotent_on_conflict(self):
        """同一 `batch_id` 因水位线未推进被重新扫入时必须静默跳过，不能抛唯一约束
        错误中断整轮通知（PR #99 复审第四轮 P1）。
        """
        session = AsyncMock()

        await record_baseline(
            session, {"period_type": "DAY", "id": 42, "processing_seconds": 300}
        )

        sql_text = session.execute.call_args.args[0]
        self.assertIn("ON CONFLICT (batch_id) DO NOTHING", str(sql_text))


if __name__ == "__main__":
    unittest.main()
