"""每日巡检摘要的单元测试：`build_lines` 是纯函数，直接测；`send` 打桩所有数据源。"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from ops_bot.daily_summary import build_lines, send


class TestBuildLines(unittest.TestCase):
    def test_healthy_with_no_unresolved_alerts(self):
        lines = build_lines(
            app_healthy=True, disk_percent=42.0, backup_age_hours=5.5, unresolved_alert_keys=[]
        )

        self.assertIn("✅ 正常", lines[0])
        self.assertIn("42.0%", lines[1])
        self.assertIn("5.5 小时前", lines[2])
        self.assertIn("无", lines[3])

    def test_never_backed_up_flagged(self):
        lines = build_lines(
            app_healthy=True, disk_percent=42.0, backup_age_hours=None, unresolved_alert_keys=[]
        )

        self.assertIn("从未成功过", lines[2])

    def test_unresolved_alerts_listed(self):
        lines = build_lines(
            app_healthy=False,
            disk_percent=90.0,
            backup_age_hours=1.0,
            unresolved_alert_keys=["disk-80", "service-down"],
        )

        self.assertIn("❌ 不可用", lines[0])
        self.assertIn("disk-80", lines[3])
        self.assertIn("service-down", lines[3])


class TestSend(unittest.IsolatedAsyncioTestCase):
    async def test_gathers_data_and_sends_card(self):
        feishu_client = AsyncMock()

        with patch("ops_bot.daily_summary.service.is_app_healthy", new=AsyncMock(return_value=True)), patch(
            "ops_bot.daily_summary.resources.disk_usage_percent", return_value=50.0
        ), patch(
            "ops_bot.daily_summary.backup.latest_backup_age_hours", new=AsyncMock(return_value=2.0)
        ), patch("ops_bot.daily_summary.OpsAsyncSessionFactory") as factory, patch(
            "ops_bot.daily_summary.crud.list_unresolved_alerts", new=AsyncMock(return_value=[])
        ):
            factory.return_value.__aenter__.return_value = AsyncMock()
            output = await send(feishu_client, "oc_1")

        feishu_client.send_card.assert_awaited_once()
        self.assertIn("50.0%", output)


if __name__ == "__main__":
    unittest.main()
