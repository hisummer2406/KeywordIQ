"""飞书文本指令分发的单元测试：打桩所有数据源，只验证路由与文案。"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from ops_bot import commands
from ops_bot.model import OpsTask


class TestDispatch(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_command_returns_help(self):
        reply = await commands.dispatch("随便说点什么")
        self.assertIn("可用指令", reply)

    async def test_status_command(self):
        with patch(
            "ops_bot.commands.service.is_app_healthy", new=AsyncMock(return_value=True)
        ), patch("ops_bot.commands.resources.disk_usage_percent", return_value=33.0), patch(
            "ops_bot.commands.backup.latest_backup_age_hours", new=AsyncMock(return_value=1.5)
        ), patch(
            "ops_bot.commands.git_ref.current_sha", return_value="a" * 40
        ), patch("ops_bot.commands.OpsAsyncSessionFactory") as factory, patch(
            "ops_bot.commands.crud.list_unresolved_alerts", new=AsyncMock(return_value=[])
        ):
            factory.return_value.__aenter__.return_value = AsyncMock()
            reply = await commands.dispatch("状态")

        self.assertIn("✅ 正常", reply)
        self.assertIn("33.0%", reply)
        self.assertIn("1.5 小时前", reply)
        self.assertIn("a" * 40, reply)
        self.assertIn("未解决告警：无", reply)

    async def test_status_command_lists_unresolved_alerts(self):
        """`ops-monitoring-alerting` spec 的「查询系统状态」场景要求一屏摘要包含
        当前部署提交与未解决告警——漏了这两项时，连接池已经饱和或有导入卡死，
        这条指令仍会显得"一切正常"（PR #99 复审第四轮 P2）。
        """
        from ops_bot.model import OpsAlertState

        alert = OpsAlertState(alert_key="pool-water-high", level="warn")
        with patch(
            "ops_bot.commands.service.is_app_healthy", new=AsyncMock(return_value=True)
        ), patch("ops_bot.commands.resources.disk_usage_percent", return_value=33.0), patch(
            "ops_bot.commands.backup.latest_backup_age_hours", new=AsyncMock(return_value=1.5)
        ), patch(
            "ops_bot.commands.git_ref.current_sha", return_value="b" * 40
        ), patch("ops_bot.commands.OpsAsyncSessionFactory") as factory, patch(
            "ops_bot.commands.crud.list_unresolved_alerts", new=AsyncMock(return_value=[alert])
        ):
            factory.return_value.__aenter__.return_value = AsyncMock()
            reply = await commands.dispatch("状态")

        self.assertIn("pool-water-high", reply)
        self.assertIn("warn", reply)

    async def test_task_command_lists_recent(self):
        task = OpsTask(id="t1", kind="BACKUP", status="SUCCEEDED", created_at=datetime.now(UTC))
        with patch("ops_bot.commands.OpsAsyncSessionFactory") as factory, patch(
            "ops_bot.commands.crud.recent_tasks", new=AsyncMock(return_value=[task])
        ):
            factory.return_value.__aenter__.return_value = AsyncMock()
            reply = await commands.dispatch("任务")

        self.assertIn("BACKUP", reply)
        self.assertIn("SUCCEEDED", reply)

    async def test_task_command_empty(self):
        with patch("ops_bot.commands.OpsAsyncSessionFactory") as factory, patch(
            "ops_bot.commands.crud.recent_tasks", new=AsyncMock(return_value=[])
        ):
            factory.return_value.__aenter__.return_value = AsyncMock()
            reply = await commands.dispatch("任务")

        self.assertIn("暂无", reply)

    async def test_log_command_missing_request_id(self):
        reply = await commands.dispatch("日志 ")
        self.assertIn("用法", reply)

    async def test_log_command_found(self):
        with patch(
            "ops_bot.commands.logs.find_by_request_id", return_value=["line1", "line2"]
        ):
            reply = await commands.dispatch("日志 req-123")

        self.assertEqual(reply, "line1\nline2")

    async def test_log_command_not_found(self):
        with patch("ops_bot.commands.logs.find_by_request_id", return_value=[]):
            reply = await commands.dispatch("日志 req-nope")

        self.assertIn("未找到", reply)

    async def test_latest_backup_command_never_backed_up(self):
        with patch(
            "ops_bot.commands.crud.latest_backup_attempt", new=AsyncMock(return_value=None)
        ), patch("ops_bot.commands.OpsAsyncSessionFactory") as factory:
            factory.return_value.__aenter__.return_value = AsyncMock()
            reply = await commands.dispatch("备份记录")

        self.assertIn("从未备份过", reply)

    async def test_latest_backup_command_shows_object_key_and_size(self):
        from ops_bot.model import OpsBackupRecord

        record = OpsBackupRecord(
            object_key="backups/postgres/20260916T030000Z.custom",
            size_bytes=512 * 1024 * 1024,
            status="SUCCEEDED",
            finished_at=datetime(2026, 9, 16, 3, 5, tzinfo=UTC),
        )
        with patch(
            "ops_bot.commands.crud.latest_backup_attempt", new=AsyncMock(return_value=record)
        ), patch("ops_bot.commands.OpsAsyncSessionFactory") as factory:
            factory.return_value.__aenter__.return_value = AsyncMock()
            reply = await commands.dispatch("备份记录")

        self.assertIn("20260916T030000Z.custom", reply)
        self.assertIn("512.0 MB", reply)

    async def test_latest_backup_command_reports_recent_failure_not_older_success(self):
        """最新一次尝试失败、更早一次成功时，「备份记录」必须报告最新的失败，不能
        静默展示那份更早的成功记录——否则操作者会以为备份健康（PR #99 复审第四轮 P2）。
        """
        from ops_bot.model import OpsBackupRecord

        failed_record = OpsBackupRecord(
            object_key=None,
            size_bytes=None,
            status="FAILED",
            failed_stage="UPLOAD",
            finished_at=datetime(2026, 9, 17, 3, 5, tzinfo=UTC),
        )
        with patch(
            "ops_bot.commands.crud.latest_backup_attempt", new=AsyncMock(return_value=failed_record)
        ), patch("ops_bot.commands.OpsAsyncSessionFactory") as factory:
            factory.return_value.__aenter__.return_value = AsyncMock()
            reply = await commands.dispatch("备份记录")

        self.assertIn("失败", reply)
        self.assertIn("UPLOAD", reply)


if __name__ == "__main__":
    unittest.main()
