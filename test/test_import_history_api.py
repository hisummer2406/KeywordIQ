"""导入历史两个端点的契约测试（TestClient + 依赖覆盖，不连库）。

覆盖 `import-history-view` spec 的可在无库环境下断言的 Scenario：分页契约形状、
每页上限、非法站点、重导的四类拒绝，以及「无 token 必拒」这条安全回归。

会话是假的（``AsyncSessionFactory`` 被替换），但**服务层的判定真的跑过**——测的不是
「端点会不会调 service」，而是「一个不合法的请求会不会被挡住」。
"""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.common.error_handlers import register_error_handlers
from app.table.upload.import_batch_model import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PROCESSING,
)


def _super_user():
    from app.user.user_model import UserCenter

    return UserCenter(id=1, user_name="root", hashed_pwd="x", is_active=True, is_super=True)


def _batch(**overrides):
    base = dict(
        id=7,
        site="DE",
        period_type="WEEK",
        report_date=date(2026, 7, 13),
        file_name="DE_热门搜索词_简单_Week_2026_07_13.csv",
        object_key="imports/DE/WEEK/2026-07-13/ab12_DE.csv",
        status=STATUS_FAILED,
        total_rows=3_000_000,
        written_rows=0,
        processing_seconds=12,
        error_message="COPY 中断",
        created_at=datetime(2026, 7, 13, 3, 0, 0),
        completed_at=None,
        stats_status=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return len(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        return _FakeResult(self._rows)


def _build_app(monkeypatch, *, rows=(), verify=None):
    """装一个只含 upload 路由的 app。会话与 OSS 校验换成替身，导入后台任务被拦掉。"""
    import app.table.upload.upload_api as upload_api_mod
    from app.auth.simple_auth import simple_auth

    monkeypatch.setattr(
        upload_api_mod.database, "AsyncSessionFactory", lambda: _FakeSession(rows)
    )

    def default_verify(object_key, site, period, report_date, *, bucket=None):
        return 123

    monkeypatch.setattr(
        upload_api_mod.import_history_service.oss_client,
        "verify_source_object",
        verify or default_verify,
    )

    # 后台任务在 TestClient 里是**真会跑**的。不拦掉，一次重导测试会去连真库跑导入。
    triggered: list[tuple] = []
    monkeypatch.setattr(
        upload_api_mod, "_run_oss_import_bg", lambda *a: triggered.append(a)
    )

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(upload_api_mod.upload_router, prefix="/api/upload")
    app.dependency_overrides[simple_auth.get_current_user] = _super_user
    app.state.triggered = triggered
    return app


# ---------------- 鉴权 ----------------


@pytest.mark.parametrize(
    "method, path",
    [("get", "/api/upload/batches"), ("post", "/api/upload/batches/7/retry")],
)
def test_endpoints_require_auth(method, path):
    """鉴权显式落在端点依赖上，没有全局中间件兜底——新端点漏挂就会在这里红。"""
    import app.table.upload.upload_api as upload_api_mod

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(upload_api_mod.upload_router, prefix="/api/upload")
    r = getattr(TestClient(app), method)(path)  # 不带 Authorization 头
    assert r.status_code in (401, 403)


# ---------------- 列表 ----------------


def test_list_returns_protable_contract(monkeypatch):
    client = TestClient(_build_app(monkeypatch, rows=[_batch(id=1), _batch(id=2)]))

    r = client.get("/api/upload/batches")

    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["total"] == 2
    assert [b["id"] for b in body["data"]] == [1, 2]
    assert "items" not in body  # 不得残留看板那份 amis 风格的形状


def test_list_row_exposes_retainability_not_object_key(monkeypatch):
    """object key 含站点/期次/日期/原始文件名，不序列化；界面要的是「还能不能重导」。"""
    key = "imports/DE/WEEK/2026-07-13/ab12_DE.csv"
    client = TestClient(_build_app(monkeypatch, rows=[_batch(object_key=key)]))

    r = client.get("/api/upload/batches")
    row = r.json()["data"][0]

    assert row["source_retained"] is True
    assert "object_key" not in row
    assert key not in r.text


def test_list_row_marks_local_import_as_not_retained(monkeypatch):
    client = TestClient(_build_app(monkeypatch, rows=[_batch(object_key="")]))
    r = client.get("/api/upload/batches")
    assert r.json()["data"][0]["source_retained"] is False


def test_list_row_carries_query_stats_status(monkeypatch):
    """界面据此在「已完成」下提示查询可能较慢；null（旧批次）原样回 null，不伪装成就绪。"""
    rows = [_batch(id=1, status="COMPLETED", stats_status="PENDING"), _batch(id=2)]
    client = TestClient(_build_app(monkeypatch, rows=rows))
    data = client.get("/api/upload/batches").json()["data"]
    assert [d["stats_status"] for d in data] == ["PENDING", None]


def test_list_rejects_per_page_over_cap(monkeypatch):
    """超上限拒绝而非静默截断——截断的分页与请求不符，界面据此算出的页码全错。"""
    client = TestClient(_build_app(monkeypatch))
    r = client.get("/api/upload/batches", params={"per_page": 500})

    assert r.status_code == 400
    assert r.json()["success"] is False


def test_list_rejects_unknown_site(monkeypatch):
    client = TestClient(_build_app(monkeypatch))
    r = client.get("/api/upload/batches", params={"site": "UK"})  # 英国是 GB 不是 UK

    assert r.status_code == 400
    body = r.json()
    assert body["success"] is False and "request_id" in body


def test_list_accepts_empty_filters(monkeypatch):
    client = TestClient(_build_app(monkeypatch))
    r = client.get("/api/upload/batches", params={"site": "", "period": "", "status": ""})
    assert r.status_code == 200


# ---------------- 重导 ----------------


def test_retry_failed_batch_triggers_import(monkeypatch):
    app = _build_app(monkeypatch, rows=[_batch()])
    client = TestClient(app)

    r = client.post("/api/upload/batches/7/retry")

    assert r.status_code == 200
    body = r.json()
    assert body["source_batch_id"] == 7
    assert (body["site"], body["period"], body["report_date"]) == ("DE", "WEEK", "2026-07-13")
    # 后台导入确实被排上；新批次记录由导入内核创建，故响应里没有新 id
    assert len(app.state.triggered) == 1
    assert "id" not in body


@pytest.mark.parametrize("status", [STATUS_COMPLETED, STATUS_PROCESSING])
def test_retry_non_failed_batch_is_409(monkeypatch, status):
    app = _build_app(monkeypatch, rows=[_batch(status=status)])
    r = TestClient(app).post("/api/upload/batches/7/retry")

    assert r.status_code == 409
    assert r.json()["code"] == "upload.retry_not_allowed"
    assert app.state.triggered == []  # 拒绝就不该有任何后台任务


def test_retry_without_retained_source_is_422(monkeypatch):
    app = _build_app(monkeypatch, rows=[_batch(object_key="")])
    r = TestClient(app).post("/api/upload/batches/7/retry")

    assert r.status_code == 422
    assert r.json()["code"] == "upload.source_not_retained"
    assert "重新上传" in r.json()["message"]
    assert app.state.triggered == []


def test_retry_missing_batch_is_404(monkeypatch):
    app = _build_app(monkeypatch, rows=[])
    r = TestClient(app).post("/api/upload/batches/999/retry")

    assert r.status_code == 404
    assert r.json()["code"] == "upload.batch_not_found"
    assert app.state.triggered == []


def test_retry_when_object_gone_does_not_schedule_import(monkeypatch):
    """对象已被清理时在触发前就拒绝，不产生一个注定失败的后台任务。"""
    from app.table.upload.oss_client import OSSObjectNotFound

    def gone(*args, **kwargs):
        raise OSSObjectNotFound(log_detail="没了")

    app = _build_app(monkeypatch, rows=[_batch()], verify=gone)
    r = TestClient(app).post("/api/upload/batches/7/retry")

    assert r.status_code == 404
    assert app.state.triggered == []
