"""采集端配置端点（competitor-asin-ingest「服务端公布单批上限」，#166 D4）。无需 PostgreSQL。"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.common.error_handlers import register_error_handlers
from app.table.competitor.asin_api import asin_router
from config import settings


class _StubUser:
    def __init__(self, permissions=()):
        self.is_super = False
        self.permission_codes = set(permissions)
        self.id = 1


def _client(user) -> TestClient:
    from app.auth.simple_auth import simple_auth

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(asin_router, prefix="/api/competitor")
    app.dependency_overrides[simple_auth.get_current_user] = lambda: user
    return TestClient(app)


def test_返回当前生效的上限且随配置变化(monkeypatch):
    client = _client(_StubUser({"competitor:view"}))
    monkeypatch.setattr(settings, "COMPETITOR_COLLECTOR_SELECTORS_FILE", "")
    assert client.get("/api/competitor/collector-config").json() == {
        "max_report_items": settings.COMPETITOR_MAX_REPORT_ITEMS,
        "selectors": None,
        "selectors_version": None,
    }
    monkeypatch.setattr(settings, "COMPETITOR_MAX_REPORT_ITEMS", 800)
    assert client.get("/api/competitor/collector-config").json()["max_report_items"] == 800


def test_默认上限是1000():
    from config import Settings

    assert Settings.model_fields["COMPETITOR_MAX_REPORT_ITEMS"].default == 1000


def test_只有分析权限时403():
    r = _client(_StubUser({"analysis:view"})).get("/api/competitor/collector-config")
    assert r.status_code == 403
