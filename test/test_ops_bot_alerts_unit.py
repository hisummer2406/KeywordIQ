"""告警去重/升级/恢复状态机的单元测试。全部打桩 crud 层，只验证状态转移与是否发送。"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from ops_bot import alerts
from ops_bot.model import OpsAlertState


def _state(**overrides) -> OpsAlertState:
    defaults = {
        "alert_key": "disk-80",
        "level": "warn",
        "last_fired_at": None,
        "consecutive_count": 0,
        "resolved_at": None,
        "maintenance_until": None,
    }
    defaults.update(overrides)
    return OpsAlertState(**defaults)


class TestFirstFire(unittest.IsolatedAsyncioTestCase):
    async def test_never_fired_before_sends_and_upserts(self):
        upsert = AsyncMock()
        send = AsyncMock()

        with patch("ops_bot.crud.get_alert_state", new=AsyncMock(return_value=None)), patch(
            "ops_bot.crud.upsert_alert_fired", upsert
        ), patch("ops_bot.alerts._send", send):
            await alerts.evaluate(
                session=None,
                feishu_client=None,
                chat_id="oc_1",
                alert_key="disk-80",
                level="crit",
                is_alerting=True,
                message="磁盘 85%",
                silence_minutes=30,
            )

        upsert.assert_awaited_once_with(None, "disk-80", "crit", 1)
        send.assert_awaited_once()


class TestSilenceSuppression(unittest.IsolatedAsyncioTestCase):
    async def test_within_silence_window_only_bumps_count_no_send(self):
        recent = datetime.now(UTC) - timedelta(minutes=5)
        state = _state(last_fired_at=recent, consecutive_count=1)
        bump = AsyncMock()
        upsert = AsyncMock()
        send = AsyncMock()

        with patch("ops_bot.crud.get_alert_state", new=AsyncMock(return_value=state)), patch(
            "ops_bot.crud.bump_alert_consecutive_count", bump
        ), patch("ops_bot.crud.upsert_alert_fired", upsert), patch("ops_bot.alerts._send", send):
            await alerts.evaluate(
                session=None,
                feishu_client=None,
                chat_id="oc_1",
                alert_key="disk-80",
                level="crit",
                is_alerting=True,
                message="磁盘 85%",
                silence_minutes=30,
            )

        bump.assert_awaited_once_with(None, "disk-80", 2)
        upsert.assert_not_awaited()
        send.assert_not_awaited()

    async def test_after_silence_window_resends(self):
        old = datetime.now(UTC) - timedelta(minutes=31)
        state = _state(last_fired_at=old, consecutive_count=1)
        upsert = AsyncMock()
        send = AsyncMock()

        with patch("ops_bot.crud.get_alert_state", new=AsyncMock(return_value=state)), patch(
            "ops_bot.crud.upsert_alert_fired", upsert
        ), patch("ops_bot.alerts._send", send):
            await alerts.evaluate(
                session=None,
                feishu_client=None,
                chat_id="oc_1",
                alert_key="disk-80",
                level="crit",
                is_alerting=True,
                message="磁盘 85%",
                silence_minutes=30,
            )

        upsert.assert_awaited_once()
        send.assert_awaited_once()


class TestEscalation(unittest.IsolatedAsyncioTestCase):
    async def test_third_consecutive_cycle_escalates_even_within_silence(self):
        recent = datetime.now(UTC) - timedelta(minutes=1)
        state = _state(last_fired_at=recent, consecutive_count=2)
        upsert = AsyncMock()
        send = AsyncMock()

        with patch("ops_bot.crud.get_alert_state", new=AsyncMock(return_value=state)), patch(
            "ops_bot.crud.upsert_alert_fired", upsert
        ), patch("ops_bot.alerts._send", send):
            await alerts.evaluate(
                session=None,
                feishu_client=None,
                chat_id="oc_1",
                alert_key="disk-80",
                level="crit",
                is_alerting=True,
                message="磁盘 85%",
                silence_minutes=30,
                escalate_after=3,
            )

        upsert.assert_awaited_once_with(None, "disk-80", "crit", 3)
        send.assert_awaited_once()
        self.assertIn("持续异常", send.call_args.args[2])


class TestRecovery(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_sends_and_resolves(self):
        state = _state(last_fired_at=datetime.now(UTC), consecutive_count=2, resolved_at=None)
        resolve = AsyncMock()
        send = AsyncMock()

        with patch("ops_bot.crud.get_alert_state", new=AsyncMock(return_value=state)), patch(
            "ops_bot.crud.mark_alert_resolved", resolve
        ), patch("ops_bot.alerts._send", send):
            await alerts.evaluate(
                session=None,
                feishu_client=None,
                chat_id="oc_1",
                alert_key="disk-80",
                level="crit",
                is_alerting=False,
                message="磁盘正常",
                silence_minutes=30,
            )

        resolve.assert_awaited_once_with(None, "disk-80")
        send.assert_awaited_once()
        self.assertIn("已恢复", send.call_args.args[2])

    async def test_recovery_not_previously_firing_is_noop(self):
        resolve = AsyncMock()
        send = AsyncMock()

        with patch("ops_bot.crud.get_alert_state", new=AsyncMock(return_value=None)), patch(
            "ops_bot.crud.mark_alert_resolved", resolve
        ), patch("ops_bot.alerts._send", send):
            await alerts.evaluate(
                session=None,
                feishu_client=None,
                chat_id="oc_1",
                alert_key="disk-80",
                level="crit",
                is_alerting=False,
                message="磁盘正常",
                silence_minutes=30,
            )

        resolve.assert_not_awaited()
        send.assert_not_awaited()


class TestMaintenanceWindow(unittest.IsolatedAsyncioTestCase):
    async def test_downgrades_level_during_maintenance(self):
        future = datetime.now(UTC) + timedelta(minutes=3)
        state = _state(maintenance_until=future)
        upsert = AsyncMock()
        send = AsyncMock()

        with patch("ops_bot.crud.get_alert_state", new=AsyncMock(return_value=state)), patch(
            "ops_bot.crud.upsert_alert_fired", upsert
        ), patch("ops_bot.alerts._send", send):
            await alerts.evaluate(
                session=None,
                feishu_client=None,
                chat_id="oc_1",
                alert_key="service-down",
                level="crit",
                is_alerting=True,
                message="服务不可用",
                silence_minutes=30,
            )

        upsert.assert_awaited_once_with(None, "service-down", "warn", 1)
        self.assertIn("维护窗口", send.call_args.args[2])


if __name__ == "__main__":
    unittest.main()
