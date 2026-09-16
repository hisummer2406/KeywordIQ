"""ops-bot webhook/health 入口的单元测试。

不用 `with TestClient(...)` 上下文管理器，因此不进入任何 lifespan（不起调度循环）
——同构 `test_monitoring_endpoint_unit.py` 的做法。`/ops/health` 仍会查一次
`ops.ops_task`，用 `AsyncMock` 顶掉 `crud.last_succeeded_at` 避免真连库；带 DB 的
幂等/鉴权分支覆盖在 `test_ops_bot_crud_integration.py`（幂等）与本文件之外的鉴权
单元测试（`is_authorized` 纯函数，见 `test_ops_bot_feishu_unit.py`）里。
"""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from ops_bot import main


def _client() -> TestClient:
    return TestClient(main.app, raise_server_exceptions=False)


class TestOpsHealth(unittest.TestCase):
    def test_no_heartbeat_yet_reports_starting(self):
        with patch("ops_bot.main.crud.last_succeeded_at", new=AsyncMock(return_value=None)):
            response = _client().get("/ops/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "starting"})

    def test_fresh_heartbeat_reports_ok(self):
        recent = datetime.now(UTC) - timedelta(seconds=5)
        with patch("ops_bot.main.crud.last_succeeded_at", new=AsyncMock(return_value=recent)):
            response = _client().get("/ops/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_stale_heartbeat_returns_503(self):
        """心跳超过容忍窗口未更新——区分「循环卡死」与「进程还能响应 HTTP」的关键路径。"""
        stale = datetime.now(UTC) - timedelta(seconds=main._HEARTBEAT_STALE_AFTER_SECONDS + 1)
        with patch("ops_bot.main.crud.last_succeeded_at", new=AsyncMock(return_value=stale)):
            response = _client().get("/ops/health")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "stale")


class TestFeishuWebhookUrlVerification(unittest.TestCase):
    def test_challenge_echoed_without_touching_db(self):
        with patch("ops_bot.main.settings") as mock_settings:
            mock_settings.FEISHU_VERIFICATION_TOKEN = "tok"
            mock_settings.FEISHU_ENCRYPT_KEY = ""

            response = _client().post(
                "/ops/feishu/webhook",
                content=json.dumps({"type": "url_verification", "token": "tok", "challenge": "abc123"}),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"challenge": "abc123"})

    def test_wrong_token_rejected_with_403(self):
        with patch("ops_bot.main.settings") as mock_settings:
            mock_settings.FEISHU_VERIFICATION_TOKEN = "tok"
            mock_settings.FEISHU_ENCRYPT_KEY = ""

            response = _client().post(
                "/ops/feishu/webhook",
                content=json.dumps({"type": "url_verification", "token": "wrong", "challenge": "abc"}),
            )

        self.assertEqual(response.status_code, 403)

    def test_malformed_body_rejected_with_403(self):
        with patch("ops_bot.main.settings") as mock_settings:
            mock_settings.FEISHU_VERIFICATION_TOKEN = "tok"
            mock_settings.FEISHU_ENCRYPT_KEY = ""

            response = _client().post("/ops/feishu/webhook", content=b"not json")

        self.assertEqual(response.status_code, 403)


class TestRunManualBackup(unittest.IsolatedAsyncioTestCase):
    """`_run_manual_backup` 是后台任务体，不经 HTTP 层——单独测，覆盖成功/失败两条路径
    各自正确落 `ops_task` 并发出对应的结果消息。
    """

    async def test_success_marks_succeeded_and_sends_result(self):
        mark_succeeded = AsyncMock()
        mark_failed = AsyncMock()
        send_text = AsyncMock()

        with patch("ops_bot.main.crud.create_task", new=AsyncMock(return_value="t-1")), patch(
            "ops_bot.main.crud.mark_task_succeeded", mark_succeeded
        ), patch("ops_bot.main.crud.mark_task_failed", mark_failed), patch(
            "ops_bot.main.backup.run_backup", new=AsyncMock(return_value="备份成功：xxx")
        ), patch("ops_bot.main.OpsAsyncSessionFactory") as factory, patch(
            "ops_bot.main._feishu_client.send_text", send_text
        ):
            factory.return_value.__aenter__.return_value = AsyncMock()
            await main._run_manual_backup("ou_owner")

        mark_succeeded.assert_awaited_once()
        mark_failed.assert_not_awaited()
        self.assertIn("✅", send_text.call_args.args[1])

    async def test_failure_marks_failed_and_sends_error(self):
        mark_succeeded = AsyncMock()
        mark_failed = AsyncMock()
        send_text = AsyncMock()

        with patch("ops_bot.main.crud.create_task", new=AsyncMock(return_value="t-1")), patch(
            "ops_bot.main.crud.mark_task_succeeded", mark_succeeded
        ), patch("ops_bot.main.crud.mark_task_failed", mark_failed), patch(
            "ops_bot.main.backup.run_backup", new=AsyncMock(side_effect=RuntimeError("boom"))
        ), patch("ops_bot.main.OpsAsyncSessionFactory") as factory, patch(
            "ops_bot.main._feishu_client.send_text", send_text
        ):
            factory.return_value.__aenter__.return_value = AsyncMock()
            await main._run_manual_backup("ou_owner")

        mark_failed.assert_awaited_once()
        mark_succeeded.assert_not_awaited()
        self.assertIn("❌", send_text.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
