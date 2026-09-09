"""关键词排名趋势详情端点的权限边界与错误映射（无需 PostgreSQL）。

覆盖 issue #86 的验收条件：两个入口各自的合法权限组合均能正常使用（只有
`analysis:view` 或只有 `keyword:view`），都没有则 403；service 层抛出的域错误
（跨站点、关键词不存在）经全局处理器映射到正确的状态码与错误码。
"""
from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.common.error_handlers import register_error_handlers
from app.table.keyword import trend_api as api_mod
from app.table.keyword import trend_service as service
from app.table.keyword.trend_schemas import KeywordTrendResponse


class _StubUser:
    def __init__(self, *, is_super=False, permissions=(), id=1):
        self.is_super = is_super
        self.permission_codes = set(permissions)
        self.id = id


_FAKE_RESPONSE = KeywordTrendResponse(
    keyword_id=1, keyword="kw", site="US", view="DAY", range="RECENT_30D", on=date(2026, 7, 1),
)


def _build_app(monkeypatch, *, user, service_fn=None):
    from app.auth.simple_auth import simple_auth
    from database import get_async_db

    async def _default(*_args, **_kwargs):
        return _FAKE_RESPONSE

    monkeypatch.setattr(service, "get_keyword_trend", service_fn or _default)

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(api_mod.trend_router, prefix="/api/keyword")
    app.dependency_overrides[get_async_db] = lambda: None
    app.dependency_overrides[simple_auth.get_current_user] = lambda: user
    return app


_PARAMS = {"keyword_id": 1, "site": "US", "view": "DAY"}


def test_analysis_view_only_can_access(monkeypatch):
    app = _build_app(monkeypatch, user=_StubUser(permissions={"analysis:view"}))
    r = TestClient(app).get("/api/keyword/trend", params=_PARAMS)
    assert r.status_code == 200


def test_keyword_view_only_can_access(monkeypatch):
    app = _build_app(monkeypatch, user=_StubUser(permissions={"keyword:view"}))
    r = TestClient(app).get("/api/keyword/trend", params=_PARAMS)
    assert r.status_code == 200


def test_neither_permission_is_rejected(monkeypatch):
    def _must_not_be_called(*_a, **_k):
        raise AssertionError("service 层不该被调用——权限检查必须在它之前拦住")

    app = _build_app(
        monkeypatch, user=_StubUser(permissions={"upload:import"}), service_fn=_must_not_be_called
    )
    r = TestClient(app).get("/api/keyword/trend", params=_PARAMS)
    assert r.status_code == 403
    assert r.json()["success"] is False


def test_super_user_bypasses_permission_check(monkeypatch):
    app = _build_app(monkeypatch, user=_StubUser(is_super=True, permissions=()))
    r = TestClient(app).get("/api/keyword/trend", params=_PARAMS)
    assert r.status_code == 200


def test_site_mismatch_maps_to_422(monkeypatch):
    async def _raise(*_a, **_k):
        raise service.KeywordTrendSiteMismatchError()

    app = _build_app(
        monkeypatch, user=_StubUser(permissions={"analysis:view"}), service_fn=_raise
    )
    r = TestClient(app).get("/api/keyword/trend", params=_PARAMS)
    assert r.status_code == 422
    assert r.json()["code"] == "keyword.trend_site_mismatch"


def test_unknown_keyword_maps_to_404(monkeypatch):
    async def _raise(*_a, **_k):
        raise service.KeywordTrendNotFoundError()

    app = _build_app(
        monkeypatch, user=_StubUser(permissions={"keyword:view"}), service_fn=_raise
    )
    r = TestClient(app).get("/api/keyword/trend", params=_PARAMS)
    assert r.status_code == 404
    assert r.json()["code"] == "keyword.trend_not_found"
