"""发布必需 CI 校验的单元测试：打桩 httpx，不打真 GitHub API。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from ops_bot.release.ci import check

_RealAsyncClient = httpx.AsyncClient


def _client_with(handler) -> httpx.AsyncClient:
    return _RealAsyncClient(transport=httpx.MockTransport(handler))


def _run(payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload)


class TestCheck(unittest.IsolatedAsyncioTestCase):
    async def test_no_token_configured_fails_closed(self):
        with patch("ops_bot.release.ci.settings") as mock_settings:
            mock_settings.GH_TOKEN = ""
            result = await check("a" * 40)

        self.assertFalse(result.passing)

    async def test_all_three_jobs_success(self):
        payload = {
            "check_runs": [
                {"name": "backend", "status": "completed", "conclusion": "success", "started_at": "2026-09-16T00:00:00Z"},
                {"name": "frontend", "status": "completed", "conclusion": "success", "started_at": "2026-09-16T00:00:00Z"},
                {"name": "extension", "status": "completed", "conclusion": "success", "started_at": "2026-09-16T00:00:00Z"},
            ]
        }

        def handler(request):
            return _run(payload)

        with patch("ops_bot.release.ci.settings") as mock_settings, patch(
            "httpx.AsyncClient", lambda **kw: _client_with(handler)
        ):
            mock_settings.GH_TOKEN = "tok"
            mock_settings.GH_REPO = "owner/repo"
            result = await check("a" * 40)

        self.assertTrue(result.passing)

    async def test_one_job_in_progress_fails(self):
        payload = {
            "check_runs": [
                {"name": "backend", "status": "in_progress", "conclusion": None, "started_at": "2026-09-16T00:00:00Z"},
                {"name": "frontend", "status": "completed", "conclusion": "success", "started_at": "2026-09-16T00:00:00Z"},
                {"name": "extension", "status": "completed", "conclusion": "success", "started_at": "2026-09-16T00:00:00Z"},
            ]
        }

        def handler(request):
            return _run(payload)

        with patch("ops_bot.release.ci.settings") as mock_settings, patch(
            "httpx.AsyncClient", lambda **kw: _client_with(handler)
        ):
            mock_settings.GH_TOKEN = "tok"
            mock_settings.GH_REPO = "owner/repo"
            result = await check("a" * 40)

        self.assertFalse(result.passing)
        self.assertIn("进行中", result.details["backend"])

    async def test_one_job_failed(self):
        payload = {
            "check_runs": [
                {"name": "backend", "status": "completed", "conclusion": "failure", "started_at": "2026-09-16T00:00:00Z"},
                {"name": "frontend", "status": "completed", "conclusion": "success", "started_at": "2026-09-16T00:00:00Z"},
                {"name": "extension", "status": "completed", "conclusion": "success", "started_at": "2026-09-16T00:00:00Z"},
            ]
        }

        def handler(request):
            return _run(payload)

        with patch("ops_bot.release.ci.settings") as mock_settings, patch(
            "httpx.AsyncClient", lambda **kw: _client_with(handler)
        ):
            mock_settings.GH_TOKEN = "tok"
            mock_settings.GH_REPO = "owner/repo"
            result = await check("a" * 40)

        self.assertFalse(result.passing)

    async def test_missing_job_fails_not_treated_as_success(self):
        payload = {
            "check_runs": [
                {"name": "backend", "status": "completed", "conclusion": "success", "started_at": "2026-09-16T00:00:00Z"},
                {"name": "frontend", "status": "completed", "conclusion": "success", "started_at": "2026-09-16T00:00:00Z"},
            ]
        }

        def handler(request):
            return _run(payload)

        with patch("ops_bot.release.ci.settings") as mock_settings, patch(
            "httpx.AsyncClient", lambda **kw: _client_with(handler)
        ):
            mock_settings.GH_TOKEN = "tok"
            mock_settings.GH_REPO = "owner/repo"
            result = await check("a" * 40)

        self.assertFalse(result.passing)
        self.assertIn("未找到", result.details["extension"])

    async def test_rerun_takes_latest_started_at(self):
        """同一 job 因重跑产生多条记录，取 started_at 最新的一条判定。"""
        payload = {
            "check_runs": [
                {"name": "backend", "status": "completed", "conclusion": "failure", "started_at": "2026-09-16T00:00:00Z"},
                {"name": "backend", "status": "completed", "conclusion": "success", "started_at": "2026-09-16T01:00:00Z"},
                {"name": "frontend", "status": "completed", "conclusion": "success", "started_at": "2026-09-16T00:00:00Z"},
                {"name": "extension", "status": "completed", "conclusion": "success", "started_at": "2026-09-16T00:00:00Z"},
            ]
        }

        def handler(request):
            return _run(payload)

        with patch("ops_bot.release.ci.settings") as mock_settings, patch(
            "httpx.AsyncClient", lambda **kw: _client_with(handler)
        ):
            mock_settings.GH_TOKEN = "tok"
            mock_settings.GH_REPO = "owner/repo"
            result = await check("a" * 40)

        self.assertTrue(result.passing)

    async def test_network_error_fails_closed(self):
        def handler(request):
            raise httpx.ConnectError("refused", request=request)

        with patch("ops_bot.release.ci.settings") as mock_settings, patch(
            "httpx.AsyncClient", lambda **kw: _client_with(handler)
        ):
            mock_settings.GH_TOKEN = "tok"
            mock_settings.GH_REPO = "owner/repo"
            result = await check("a" * 40)

        self.assertFalse(result.passing)


if __name__ == "__main__":
    unittest.main()
