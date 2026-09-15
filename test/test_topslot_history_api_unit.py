"""ASIN/品牌悬浮历史端点的权限边界与错误映射（无需 PostgreSQL）。

覆盖验收条件「新接口校验站点、参数和权限，未登录／无市场分析权限不能读取」。
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.common.error_handlers import register_error_handlers
from app.table.analysis import analysis_api as api_mod
from app.table.analysis import topslot_history_service as service
from app.table.analysis.topslot_history_schemas import TopslotHistoryResponse


class _StubUser:
    def __init__(self, *, is_super=False, permissions=(), id=1):
        self.is_super = is_super
        self.permission_codes = set(permissions)
        self.id = id


_FAKE_RESPONSE = TopslotHistoryResponse(total=0, items=[])
_PARAMS = {
    "site": "US", "view": "DAY", "keyword_id": 1, "kind": "ASIN", "value": "B0EXAMPLE1",
    "on": "2026-07-01",
}


def _build_app(monkeypatch, *, user, service_fn=None):
    from app.auth.simple_auth import simple_auth
    from database import get_async_db, get_db

    async def _default(*_args, **_kwargs):
        return _FAKE_RESPONSE

    monkeypatch.setattr(service, "get_topslot_history", service_fn or _default)

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(api_mod.analysis_router, prefix="/api/analysis")
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[get_async_db] = lambda: None
    app.dependency_overrides[simple_auth.get_current_user] = lambda: user
    return app


def test_analysis_view_can_access(monkeypatch):
    app = _build_app(monkeypatch, user=_StubUser(permissions={"analysis:view"}))
    r = TestClient(app).get("/api/analysis/topslot-history", params=_PARAMS)
    assert r.status_code == 200


def test_no_permission_is_rejected(monkeypatch):
    def _must_not_be_called(*_a, **_k):
        raise AssertionError("service 层不该被调用——权限检查必须在它之前拦住")

    app = _build_app(
        monkeypatch, user=_StubUser(permissions={"upload:import"}), service_fn=_must_not_be_called
    )
    r = TestClient(app).get("/api/analysis/topslot-history", params=_PARAMS)
    assert r.status_code == 403
    assert r.json()["success"] is False


def test_unauthenticated_is_rejected(monkeypatch):
    """未登录：不覆盖 get_current_user 依赖，让它走真实鉴权路径拒绝无凭证请求。"""
    from database import get_async_db, get_db

    def _must_not_be_called(*_a, **_k):
        raise AssertionError("service 层不该被调用")

    monkeypatch.setattr(service, "get_topslot_history", _must_not_be_called)

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(api_mod.analysis_router, prefix="/api/analysis")
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[get_async_db] = lambda: None

    r = TestClient(app).get("/api/analysis/topslot-history", params=_PARAMS)
    # 未登录：具体是 401 还是 403 取决于凭证缺失被判定的层级（同既有端点的既有测试口径，
    # 见 test_protable_contract.py / test_analysis_export_unit.py），只要求不是 200。
    assert r.status_code in (401, 403)


def test_super_user_bypasses_permission_check(monkeypatch):
    app = _build_app(monkeypatch, user=_StubUser(is_super=True, permissions=()))
    r = TestClient(app).get("/api/analysis/topslot-history", params=_PARAMS)
    assert r.status_code == 200


def test_site_mismatch_maps_to_422(monkeypatch):
    async def _raise(*_a, **_k):
        raise service.TopslotSiteMismatchError()

    app = _build_app(monkeypatch, user=_StubUser(permissions={"analysis:view"}), service_fn=_raise)
    r = TestClient(app).get("/api/analysis/topslot-history", params=_PARAMS)
    assert r.status_code == 422
    assert r.json()["code"] == "analysis.topslot_site_mismatch"


def test_unknown_keyword_maps_to_404(monkeypatch):
    async def _raise(*_a, **_k):
        raise service.TopslotKeywordNotFoundError()

    app = _build_app(monkeypatch, user=_StubUser(permissions={"analysis:view"}), service_fn=_raise)
    r = TestClient(app).get("/api/analysis/topslot-history", params=_PARAMS)
    assert r.status_code == 404
    assert r.json()["code"] == "analysis.topslot_keyword_not_found"


def test_page_size_over_limit_maps_to_400(monkeypatch):
    async def _raise(*_a, **_k):
        raise service.TopslotPageSizeExceededError("每页条数最多 20 条，收到 999")

    app = _build_app(monkeypatch, user=_StubUser(permissions={"analysis:view"}), service_fn=_raise)
    r = TestClient(app).get(
        "/api/analysis/topslot-history", params={**_PARAMS, "page_size": 999}
    )
    assert r.status_code == 400
    assert r.json()["code"] == "analysis.topslot_page_size_exceeded"


def test_missing_required_query_param_is_rejected(monkeypatch):
    app = _build_app(monkeypatch, user=_StubUser(permissions={"analysis:view"}))
    params = {k: v for k, v in _PARAMS.items() if k != "on"}
    r = TestClient(app).get("/api/analysis/topslot-history", params=params)
    assert r.status_code == 422
