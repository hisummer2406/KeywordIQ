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
        ):
            reply = await commands.dispatch("状态")

        self.assertIn("✅ 正常", reply)
        self.assertIn("33.0%", reply)
        self.assertIn("1.5 小时前", reply)

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
            "ops_bot.commands.crud.latest_succeeded_backup", new=AsyncMock(return_value=None)
        ), patch("ops_bot.commands.OpsAsyncSessionFactory") as factory:
            factory.return_value.__aenter__.return_value = AsyncMock()
            reply = await commands.dispatch("备份记录")

        self.assertIn("从未成功备份过", reply)

    async def test_latest_backup_command_shows_object_key_and_size(self):
        from ops_bot.model import OpsBackupRecord

        record = OpsBackupRecord(
            object_key="backups/postgres/20260916T030000Z.custom",
            size_bytes=512 * 1024 * 1024,
            status="SUCCEEDED",
            finished_at=datetime(2026, 9, 16, 3, 5, tzinfo=UTC),
        )
        with patch(
            "ops_bot.commands.crud.latest_succeeded_backup", new=AsyncMock(return_value=record)
        ), patch("ops_bot.commands.OpsAsyncSessionFactory") as factory:
            factory.return_value.__aenter__.return_value = AsyncMock()
            reply = await commands.dispatch("备份记录")

        self.assertIn("20260916T030000Z.custom", reply)
        self.assertIn("512.0 MB", reply)


if __name__ == "__main__":
    unittest.main()
