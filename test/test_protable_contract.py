"""ProTable 响应契约测试（无需 PostgreSQL）。

保护迁移不回归 amis 形状：断言适配层与受影响列表端点产出 {data, total, success}。
覆盖 design 决策 3 与 admin-spa spec 的成功/失败两个 Scenario。
"""
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.common.responses import ProTableResponse, protable, protable_error
from app.user.user_schemas import UserCenterList


# ---------------- 适配层单元 ----------------

def test_protable_success_shape():
    resp = protable([{"a": 1}, {"a": 2}], total=42)
    dumped = resp.model_dump()
    assert dumped["success"] is True
    assert dumped["data"] == [{"a": 1}, {"a": 2}]
    assert dumped["total"] == 42


def test_protable_error_shape():
    resp = protable_error("boom")
    dumped = resp.model_dump()
    assert dumped["success"] is False
    assert dumped["data"] == []
    assert dumped["total"] == 0
    assert dumped["message"] == "boom"


def test_protable_keys_are_exactly_the_contract():
    keys = set(protable([], 0).model_dump().keys())
    # 契约字段：data/total/success（message 可选附带）
    assert {"data", "total", "success"} <= keys
    assert "items" not in keys and "status" not in keys  # 不得残留 amis 形状


# ---------------- 端点契约（TestClient + 依赖覆盖，无 DB） ----------------

def _make_user_list_item(uid: int) -> UserCenterList:
    now = datetime(2026, 7, 29, 12, 0, 0)
    return UserCenterList(
        id=uid, user_name=f"u{uid}", is_active=True, is_super=False,
        created_at=now, updated_at=now,
    )


def _super_user():
    from app.user.user_model import UserCenter
    return UserCenter(id=1, user_name="root", hashed_pwd="x", is_active=True, is_super=True)


def _build_app(monkeypatch, *, paginated=None, raises=False):
    from database import get_db
    from app.auth.simple_auth import simple_auth
    import app.user.user_api as user_api_mod

    def fake_paginated(self, **kwargs):
        if raises:
            raise RuntimeError("db down")
        return paginated

    monkeypatch.setattr(user_api_mod.UserCenterCRUD, "get_users_paginated", fake_paginated)

    app = FastAPI()
    app.include_router(user_api_mod.user_router, prefix="/api/user")
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[simple_auth.get_current_user] = _super_user
    return app


def test_user_table_requires_auth():
    """安全回归：无 Authorization 头访问受保护端点应被拒（鉴权落在端点依赖，不靠中间件）。

    历史上曾有一个 exclude_paths 含 '/' 的中间件形同虚设，导致部分端点裸奔；阶段七删中间件、
    鉴权全部显式落到端点依赖后，此用例守住"无 token 必拒"。
    """
    import app.user.user_api as user_api_mod

    app = FastAPI()
    app.include_router(user_api_mod.user_router, prefix="/api/user")
    client = TestClient(app)
    r = client.get("/api/user/table")  # 不带 Authorization 头
    assert r.status_code in (401, 403)


def test_user_table_endpoint_returns_protable_contract(monkeypatch):
    paginated = {"items": [_make_user_list_item(1), _make_user_list_item(2)], "total": 2,
                 "page": 1, "per_page": 20, "pages": 1}
    client = TestClient(_build_app(monkeypatch, paginated=paginated))

    r = client.get("/api/user/table")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["total"] == 2
    assert [u["id"] for u in body["data"]] == [1, 2]
    assert "items" not in body and "status" not in body


def test_user_table_endpoint_failure_shape(monkeypatch):
    client = TestClient(_build_app(monkeypatch, raises=True))
    r = client.get("/api/user/table")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert body["data"] == [] and body["total"] == 0
