"""ops-bot webhook 入口的单元测试：只覆盖不触库的两条路径（URL 校验挑战、来源校验拒绝）。

不用 `with TestClient(...)` 上下文管理器，因此不进入任何 lifespan，不会尝试连接
`OPS_DATABASE_URL`——同构 `test_monitoring_endpoint_unit.py` 的做法。带 DB 的幂等/鉴权
分支覆盖在 `test_ops_bot_crud_integration.py`（幂等）与本文件的鉴权单元测试
（`is_authorized` 纯函数，见 `test_ops_bot_feishu_unit.py`）里。
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from ops_bot import main


def _client() -> TestClient:
    return TestClient(main.app, raise_server_exceptions=False)


class TestOpsHealth(unittest.TestCase):
    def test_health_returns_ok(self):
        response = _client().get("/ops/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


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


if __name__ == "__main__":
    unittest.main()
