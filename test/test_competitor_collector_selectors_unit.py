"""采集端页面选择器覆盖（extension-remote-selectors）。无需 PostgreSQL。"""
import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.common.error_handlers import register_error_handlers
from app.table.competitor import collector_selectors
from app.table.competitor.asin_api import asin_router
from config import settings


class _StubUser:
    is_super = False
    permission_codes = {"competitor:view"}
    id = 1


def _client() -> TestClient:
    from app.auth.simple_auth import simple_auth

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(asin_router, prefix="/api/competitor")
    app.dependency_overrides[simple_auth.get_current_user] = lambda: _StubUser()
    return TestClient(app)


VALID = {
    "version": "2026-11-26a",
    "selectors": {
        "result_card": ['[data-component-type="s-search-result"][data-asin]'],
        "delivery": ["#glow-ingress-line2"],
        "blocked_text": ["access restricted"],
    },
}


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    monkeypatch.setattr(collector_selectors, "_cache", None)


def _write(path, data) -> None:
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")


def _get():
    return _client().get("/api/competitor/collector-config").json()


def test_未配置时不下发(monkeypatch):
    monkeypatch.setattr(settings, "COMPETITOR_COLLECTOR_SELECTORS_FILE", "")
    body = _get()
    assert body["selectors"] is None and body["selectors_version"] is None
    assert body["max_report_items"] == settings.COMPETITOR_MAX_REPORT_ITEMS


def test_合法文件按原样下发且省略未配置的键(tmp_path, monkeypatch):
    path = tmp_path / "selectors.json"
    _write(path, VALID)
    monkeypatch.setattr(settings, "COMPETITOR_COLLECTOR_SELECTORS_FILE", str(path))
    body = _get()
    assert body["selectors_version"] == "2026-11-26a"
    assert body["selectors"] == VALID["selectors"]


def test_改文件后无需重启即生效(tmp_path, monkeypatch):
    path = tmp_path / "selectors.json"
    _write(path, VALID)
    monkeypatch.setattr(settings, "COMPETITOR_COLLECTOR_SELECTORS_FILE", str(path))
    assert _get()["selectors_version"] == "2026-11-26a"

    _write(path, {**VALID, "version": "2026-11-26b"})
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))  # 保证 mtime 变化
    assert _get()["selectors_version"] == "2026-11-26b"


@pytest.mark.parametrize(
    "bad",
    [
        {**VALID, "selectors": {"unknown_key": ["x"]}},
        {**VALID, "extra": 1},
        {"selectors": VALID["selectors"]},
        {**VALID, "version": ""},
        {**VALID, "selectors": {"next_page": []}},
        {**VALID, "selectors": {"next_page": ["a"] * 11}},
        {**VALID, "selectors": {"next_page": ["x" * 201]}},
        {**VALID, "selectors": {"next_page": ["  "]}},
        {**VALID, "selectors": {"next_page": "a.next"}},
        "{not json",
    ],
)
def test_不合法时整体不下发但上限照常(tmp_path, monkeypatch, caplog, bad):
    path = tmp_path / "selectors.json"
    _write(path, bad)
    monkeypatch.setattr(settings, "COMPETITOR_COLLECTOR_SELECTORS_FILE", str(path))
    body = _get()
    assert body["selectors"] is None and body["selectors_version"] is None
    assert body["max_report_items"] == settings.COMPETITOR_MAX_REPORT_ITEMS
    assert "选择器文件无效" in caplog.text


def test_文件不存在时不下发并告警(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(settings, "COMPETITOR_COLLECTOR_SELECTORS_FILE", str(tmp_path / "missing.json"))
    body = _get()
    assert body["selectors"] is None
    assert "不存在或无法读取" in caplog.text
