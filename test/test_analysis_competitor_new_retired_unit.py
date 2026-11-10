"""已下架的「竞对新发现」参数（#166，site-scoped-query「已下架的竞对新发现参数明确拒绝」）。

无需 PostgreSQL。`competitor_new=true` 必须在请求模型解析阶段就被拒为 422——早于任何
数据库访问，`/export` 则早于开流。所以 service 层被换成"一旦被调用就让测试失败"的桩：
只要拒绝发生在它们之后，用例就会失败。`false`/不传必须照常放行，且不再要求
`competitor:view`。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.common.error_handlers import register_error_handlers
from app.table.analysis import analysis_api as api_mod
from app.table.analysis import analysis_service as service
from app.table.search.search_schemas import AnalysisSearchRequest, AnalysisSearchResponse


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
    raise AssertionError("service 层不该被调用——退役参数必须在它之前被拒")


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


_ALL = {"analysis:view", "analysis:export", "competitor:view"}


@pytest.mark.parametrize("view", ["DAY", "WEEK"])
@pytest.mark.parametrize("path", ["/api/analysis/search", "/api/analysis/export"])
def test_competitor_new_true_is_rejected_before_service(monkeypatch, path, view):
    """日/周、列表/导出一律 422；即便拥有 competitor:view 也一样——这个参数已没有可授权的含义。"""
    app = _build_app(monkeypatch, user=_StubUser(permissions=_ALL))
    r = TestClient(app).post(path, json={"site": "US", "view": view, "competitor_new": True})

    assert r.status_code == 422
    body = r.json()
    assert body["success"] is False
    assert "已下架" in r.text
    # 导出未开流的证据：响应是 JSON 错误体，没有 CSV 附件头
    assert "content-disposition" not in r.headers
    assert r.headers["content-type"].startswith("application/json")


async def _stub_search(*_args, **_kwargs) -> AnalysisSearchResponse:
    return AnalysisSearchResponse(success=True, data=[], total=0, site="US", view="DAY", date=None)


@pytest.mark.parametrize("value", [False, None])
def test_search_false_or_null_passes_without_competitor_view(monkeypatch, value):
    """只有 analysis:view 的用户传 false/null 照常放行，不再按请求内容要求 competitor:view。"""
    app = _build_app(monkeypatch, user=_StubUser(permissions={"analysis:view"}))
    seen = {}

    async def _capture(_db, req, **_kw):
        seen["req"] = req
        return await _stub_search()

    monkeypatch.setattr(service, "search", _capture)

    r = TestClient(app).post(
        "/api/analysis/search", json={"site": "US", "view": "DAY", "competitor_new": value}
    )

    assert r.status_code == 200
    assert seen["req"].competitor_new is None


def test_export_false_passes_without_competitor_view(monkeypatch):
    """导出同样：只有 analysis:export 时 false 放行到 service 层。"""
    app = _build_app(monkeypatch, user=_StubUser(permissions={"analysis:export"}))
    monkeypatch.setattr(service, "resolve_export_scope", lambda _db, _req: ("US", "DAY", None))
    monkeypatch.setattr(service, "iter_export_csv", lambda *_a: iter(["﻿keyword\n"]))

    r = TestClient(app).post(
        "/api/analysis/export", json={"site": "US", "view": "DAY", "competitor_new": False}
    )

    assert r.status_code == 200
    assert "content-disposition" in r.headers


def test_model_normalises_false_to_none():
    """false 与不传在模型层就完全一致，后续「是否带筛选」与结果键无需再特判。"""
    assert AnalysisSearchRequest(competitor_new=False).competitor_new is None
    assert AnalysisSearchRequest().competitor_new is None
