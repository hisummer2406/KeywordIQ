"""每日巡检摘要的单元测试：`build_lines` 是纯函数，直接测；`send` 打桩所有数据源。"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from ops_bot.daily_summary import build_lines, send


class TestBuildLines(unittest.TestCase):
    def test_healthy_with_no_unresolved_alerts(self):
        lines = build_lines(
            app_healthy=True, disk_percent=42.0, unresolved_alert_keys=[]
        )

        self.assertIn("✅ 正常", lines[0])
        self.assertIn("42.0%", lines[1])
        self.assertIn("无", lines[2])

    def test_no_backup_line(self):
        """不做备份：摘要里不出现备份行，更不能出现「从未成功过」这类常驻告警。"""
        lines = build_lines(app_healthy=True, disk_percent=42.0, unresolved_alert_keys=[])

        self.assertFalse(any("备份" in line for line in lines))

    def test_unresolved_alerts_listed(self):
        lines = build_lines(
            app_healthy=False,
            disk_percent=90.0,
            unresolved_alert_keys=["disk-80", "service-down"],
        )

        self.assertIn("❌ 不可用", lines[0])
        self.assertIn("disk-80", lines[2])
        self.assertIn("service-down", lines[2])


class TestSend(unittest.IsolatedAsyncioTestCase):
    async def test_gathers_data_and_sends_card(self):
        feishu_client = AsyncMock()

        with patch("ops_bot.daily_summary.service.is_app_healthy", new=AsyncMock(return_value=True)), patch(
            "ops_bot.daily_summary.resources.disk_usage_percent", return_value=50.0
        ), patch("ops_bot.daily_summary.OpsAsyncSessionFactory") as factory, patch(
            "ops_bot.daily_summary.crud.list_unresolved_alerts", new=AsyncMock(return_value=[])
        ):
            factory.return_value.__aenter__.return_value = AsyncMock()
            output = await send(feishu_client, "oc_1")

        feishu_client.send_card.assert_awaited_once()
        self.assertIn("50.0%", output)


if __name__ == "__main__":
    unittest.main()
