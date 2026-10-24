"""保护性拒绝的诊断按窗口去重（#154 request-diagnostics-logging，design D6）。无需 PostgreSQL。"""

from __future__ import annotations

import logging

import pytest
from starlette.requests import Request

from app.common import error_handlers
from app.common.log_limiter import WindowedLogLimiter, suppressed_note


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_first_in_window_passes_rest_are_counted_and_reported_next_window():
    clock = _Clock()
    limiter = WindowedLogLimiter(window_seconds=60, clock=clock)
    assert limiter.admit("busy") == 0
    assert limiter.admit("busy") is None
    clock.now = 59.9
    assert limiter.admit("busy") is None
    # 不同键互不影响
    assert limiter.admit("timeout") == 0
    clock.now = 60.0
    assert limiter.admit("busy") == 2  # 上个窗口省略了 2 次
    assert limiter.admit("busy") is None


def test_key_count_is_bounded():
    limiter = WindowedLogLimiter(window_seconds=60, max_keys=2, clock=_Clock())
    for key in ("a", "b", "c"):
        assert limiter.admit(key) == 0
    # 最久的 a 被淘汰，再出现时当作新键放行
    assert limiter.admit("a") == 0
    assert limiter.admit("c") is None


def test_suppressed_note():
    assert suppressed_note(0) == ""
    assert "3 次" in suppressed_note(3)


@pytest.fixture()
def limiter(monkeypatch):
    clock = _Clock()
    fresh = WindowedLogLimiter(window_seconds=60, clock=clock)
    monkeypatch.setattr(error_handlers, "diagnostic_limiter", fresh)
    return clock


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/api/analysis/search", "headers": []})


async def test_protective_rejections_log_once_per_window_without_stack(limiter, caplog):
    from app.table.analysis.analysis_service import AnalysisBusyError

    with caplog.at_level(logging.WARNING, logger="app.common.error_handlers"):
        for _ in range(5):
            response = await error_handlers.api_error_handler(_request(), AnalysisBusyError())
            assert response.status_code == 503
        limiter.now = 61
        await error_handlers.api_error_handler(_request(), AnalysisBusyError())

    records = [r for r in caplog.records if "保护性拒绝" in r.getMessage()]
    assert len(records) == 2
    assert all(r.levelno == logging.WARNING and r.exc_info is None for r in records)
    assert "另有 4 次" in records[1].getMessage()


async def test_other_503_errors_still_log_with_stack(limiter, caplog):
    """真正的外部故障（如 SSO 提供方不可用）不去重、照样带栈。"""
    from app.auth.sso_errors import SsoProviderUnavailableError

    with caplog.at_level(logging.ERROR, logger="app.common.error_handlers"):
        for _ in range(2):
            try:
                raise SsoProviderUnavailableError()
            except SsoProviderUnavailableError as exc:
                await error_handlers.api_error_handler(_request(), exc)

    records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(records) == 2 and all(r.exc_info is not None for r in records)
