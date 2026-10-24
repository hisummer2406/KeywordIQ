"""「竞对新发现」筛选的权限边界（无需 PostgreSQL）。

覆盖 issue #56 P2 PR #66 复审 P1：`competitor_new=true` 读取的是竞对监控域的私有
数据（用户关注了哪些卖家），只有 `analysis:view`/`analysis:export` 不足以授权。
两个端点都要在触达 service 层之前用 `competitor:view` 拦住——检查点必须在数据库
访问之前，所以这里把 `service.search`/`resolve_export_scope`/`iter_export_csv`
换成"一旦被调用就让测试失败"的桩，而不是常规意义上的 mock。
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.common.error_handlers import register_error_handlers
from app.table.analysis import analysis_api as api_mod
from app.table.analysis import analysis_service as service
from app.table.search.search_schemas import AnalysisSearchResponse


class _StubUser:
    def __init__(self, *, is_super=False, permissions=(), id=1):
        self.is_super = is_super
        self.permission_codes = set(permissions)
        self.id = id


class _StubAsyncSession:
    """/search 的并发闸排队前会 ``close()`` 请求会话，其余不碰。"""

    async def close(self) -> None:
        pass


def _must_not_be_called(*_args, **_kwargs):
    raise AssertionError("service 层不该被调用——权限检查必须在它之前拦住")


def _build_app(monkeypatch, *, user):
    from app.auth.simple_auth import simple_auth
    from database import get_async_db, get_db

    monkeypatch.setattr(service, "search", _must_not_be_called)
    monkeypatch.setattr(service, "resolve_export_scope", _must_not_be_called)
    monkeypatch.setattr(service, "iter_export_csv", _must_not_be_called)

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(api_mod.analysis_router, prefix="/api/analysis")
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[get_async_db] = _StubAsyncSession
    app.dependency_overrides[simple_auth.get_current_user] = lambda: user
    return app


_BODY = {"site": "US", "view": "DAY", "competitor_new": True}


def test_search_rejects_competitor_new_without_competitor_view(monkeypatch):
    """只有 analysis:view，没有 competitor:view：competitor_new=true 必须 403。"""
    app = _build_app(monkeypatch, user=_StubUser(permissions={"analysis:view"}))
    r = TestClient(app).post("/api/analysis/search", json=_BODY)

    assert r.status_code == 403
    body = r.json()
    assert body["success"] is False
    assert body["code"] == "auth.forbidden"


def test_export_rejects_competitor_new_without_competitor_view(monkeypatch):
    """导出同一条纪律：只有 analysis:export 不够，且检查必须在开流之前。"""
    app = _build_app(monkeypatch, user=_StubUser(permissions={"analysis:export"}))
    r = TestClient(app).post("/api/analysis/export", json=_BODY)

    assert r.status_code == 403
    body = r.json()
    assert body["success"] is False
    assert body["code"] == "auth.forbidden"
    # 未开流的证据：没有 CSV 的 Content-Disposition，响应是 JSON 错误体
    assert "content-disposition" not in r.headers
    assert r.headers["content-type"].startswith("application/json")


async def _stub_search(*_args, **_kwargs) -> AnalysisSearchResponse:
    return AnalysisSearchResponse(success=True, data=[], total=0, site="US", view="DAY", date=None)


def test_search_without_competitor_new_does_not_require_competitor_view(monkeypatch):
    """不勾竞对新发现时，只有 analysis:view 照常放行——这条权限校验不该误伤其余筛选。"""
    app = _build_app(monkeypatch, user=_StubUser(permissions={"analysis:view"}))
    monkeypatch.setattr(service, "search", _stub_search)

    r = TestClient(app).post(
        "/api/analysis/search", json={"site": "US", "view": "DAY", "competitor_new": None}
    )

    assert r.status_code == 200


def test_search_allows_competitor_new_with_competitor_view(monkeypatch):
    """两个权限点都有时，competitor_new=true 正常放行到 service 层。"""
    app = _build_app(
        monkeypatch, user=_StubUser(permissions={"analysis:view", "competitor:view"})
    )
    monkeypatch.setattr(service, "search", _stub_search)

    r = TestClient(app).post("/api/analysis/search", json=_BODY)

    assert r.status_code == 200
