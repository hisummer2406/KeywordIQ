"""发布必需 CI 校验的单元测试：打桩 httpx，不打真 GitHub API。

所有 fixture 的 `name` 字段一律取自 `REQUIRED_CHECK_NAMES` 本身，不手写
"backend"/"frontend"/"extension" 这类 job id——那正是 PR #99 Codex 复审 P1
发现的问题：GitHub 的 check-runs API 按 job 的 `name:` 展示名索引，不是 job
id，旧测试用 job id 当 fixture 的 `name`，与旧实现的 `REQUIRED_CHECK_NAMES`
互相吻合但都与真实 GitHub 响应对不上，掩盖了这个 bug。
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from ops_bot.release.ci import REQUIRED_CHECK_NAMES, check

_RealAsyncClient = httpx.AsyncClient

_NAME_A, _NAME_B, _NAME_C = REQUIRED_CHECK_NAMES


def _client_with(handler) -> httpx.AsyncClient:
    return _RealAsyncClient(transport=httpx.MockTransport(handler))


def _run(payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload)


def _run_entry(name: str, *, status="completed", conclusion="success", started_at="2026-09-16T00:00:00Z"):
    return {"name": name, "status": status, "conclusion": conclusion, "started_at": started_at}


class TestRequiredCheckNamesMatchWorkflow(unittest.TestCase):
    """`REQUIRED_CHECK_NAMES` 必须与 `.github/workflows/ci.yml` 里各 job 的
    `name:` 逐字一致——这两处只能靠字符串巧合保持同步，没有任何机制强制它们
    不漂移，漏改的表现是 CI 全绿时发布仍被判定为"未找到"而拒绝。
    """

    def test_matches_workflow_job_names(self):
        ci_yml = (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
        ).read_text(encoding="utf-8")
        for expected in REQUIRED_CHECK_NAMES:
            self.assertIn(
                f"name: {expected}", ci_yml,
                f"{expected!r} 未在 ci.yml 中以 `name: ...` 原样出现，"
                "REQUIRED_CHECK_NAMES 与工作流已经漂移",
            )


class TestCheck(unittest.IsolatedAsyncioTestCase):
    async def test_no_token_configured_fails_closed(self):
        with patch("ops_bot.release.ci.settings") as mock_settings:
            mock_settings.GH_TOKEN = ""
            result = await check("a" * 40)

        self.assertFalse(result.passing)

    async def test_all_three_jobs_success(self):
        payload = {"check_runs": [_run_entry(_NAME_A), _run_entry(_NAME_B), _run_entry(_NAME_C)]}

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
                _run_entry(_NAME_A, status="in_progress", conclusion=None),
                _run_entry(_NAME_B),
                _run_entry(_NAME_C),
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
        self.assertIn("进行中", result.details[_NAME_A])

    async def test_one_job_failed(self):
        payload = {
            "check_runs": [
                _run_entry(_NAME_A, conclusion="failure"),
                _run_entry(_NAME_B),
                _run_entry(_NAME_C),
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
        payload = {"check_runs": [_run_entry(_NAME_A), _run_entry(_NAME_B)]}

        def handler(request):
            return _run(payload)

        with patch("ops_bot.release.ci.settings") as mock_settings, patch(
            "httpx.AsyncClient", lambda **kw: _client_with(handler)
        ):
            mock_settings.GH_TOKEN = "tok"
            mock_settings.GH_REPO = "owner/repo"
            result = await check("a" * 40)

        self.assertFalse(result.passing)
        self.assertIn("未找到", result.details[_NAME_C])

    async def test_rerun_takes_latest_started_at(self):
        """同一 job 因重跑产生多条记录，取 started_at 最新的一条判定。"""
        payload = {
            "check_runs": [
                _run_entry(_NAME_A, conclusion="failure", started_at="2026-09-16T00:00:00Z"),
                _run_entry(_NAME_A, conclusion="success", started_at="2026-09-16T01:00:00Z"),
                _run_entry(_NAME_B),
                _run_entry(_NAME_C),
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
