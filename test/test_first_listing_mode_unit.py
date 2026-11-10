"""首次口径参数（#178，site-scoped-query「首次入榜筛选与当期数量同一口径」）的接口层单元测试。

无需 PostgreSQL：`first_listed=true` 与 `first_listing=PERIOD` 矛盾必须在请求模型阶段 422，早于
service（导出早于开流）；数量接口把 `mode` 原样交给 service，缺省 MONITOR；导出两列固定都出。
"""
import pytest
from fastapi.testclient import TestClient

from app.table.analysis import analysis_service as service
from app.table.search.search_schemas import AnalysisSearchRequest
from test.test_analysis_competitor_new_retired_unit import _ALL, _build_app, _StubUser


@pytest.mark.parametrize("view", ["DAY", "WEEK"])
@pytest.mark.parametrize("path", ["/api/analysis/search", "/api/analysis/export"])
def test_conflicting_first_listing_is_rejected_before_service(monkeypatch, path, view):
    app = _build_app(monkeypatch, user=_StubUser(permissions=_ALL))
    r = TestClient(app).post(
        path, json={"site": "US", "view": view, "first_listed": True, "first_listing": "PERIOD"}
    )
    assert r.status_code == 422
    assert r.json()["success"] is False
    assert "content-disposition" not in r.headers


def test_model_folds_legacy_flag():
    assert AnalysisSearchRequest(first_listed=True).first_listing == "MONITOR"
    assert AnalysisSearchRequest(first_listed=True).first_listed is None
    assert AnalysisSearchRequest(first_listed=False).first_listing is None
    assert AnalysisSearchRequest(first_listing="PERIOD").first_listing == "PERIOD"


def test_invalid_mode_rejected(monkeypatch):
    app = _build_app(monkeypatch, user=_StubUser(permissions=_ALL))
    r = TestClient(app).post("/api/analysis/search", json={"first_listing": "NEW"})
    assert r.status_code == 422


@pytest.mark.parametrize(("query", "expected"), [("", "MONITOR"), ("&mode=PERIOD", "PERIOD")])
def test_first_listed_endpoint_passes_mode(monkeypatch, query, expected):
    app = _build_app(monkeypatch, user=_StubUser(permissions=_ALL))
    seen = {}

    async def _capture(_db, site, view, on, mode):
        seen["mode"] = mode

    monkeypatch.setattr(service, "get_first_listed", _capture)
    r = TestClient(app).get(f"/api/analysis/first-listed?site=US&view=DAY{query}")
    assert r.status_code == 200
    assert seen["mode"] == expected


@pytest.mark.parametrize(("view", "label"), [("DAY", "首次进入日榜"), ("WEEK", "首次进入周榜")])
def test_export_has_both_first_columns_in_fixed_position(view, label):
    cols = service._export_columns(view)
    keys = [k for k, _ in cols]
    at = keys.index("first_listed")
    assert cols[at] == ("first_listed", "首次入榜")  # 表头不变，下游不破
    assert cols[at + 1] == ("period_first_listed", label)
