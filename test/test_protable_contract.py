"""ProTable 响应契约测试（无需 PostgreSQL）。

保护迁移不回归 amis 形状：断言适配层与受影响列表端点产出 {data, total, success}。
覆盖 design 决策 3 与 admin-spa spec 的成功/失败两个 Scenario。
"""
from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.common.error_handlers import register_error_handlers
from app.common.responses import protable
from app.user.user_schemas import UserCenterList

# ---------------- 适配层单元 ----------------

def test_protable_success_shape():
    resp = protable([{"a": 1}, {"a": 2}], total=42)
    dumped = resp.model_dump()
    assert dumped["success"] is True
    assert dumped["data"] == [{"a": 1}, {"a": 2}]
    assert dumped["total"] == 42


def test_protable_has_no_failure_constructor():
    """列表契约只表达成功。失败走统一错误契约的非 2xx，不再有 protable_error。

    此前失败以 HTTP 200 + success:false 返回，前端无法把"查询失败"与"没有数据"区分开。
    """
    import app.common.responses as responses

    assert not hasattr(responses, "protable_error")


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
    import app.user.user_api as user_api_mod
    from app.auth.simple_auth import simple_auth
    from database import get_async_db

    # 用户列表路径已异步化（AsyncSession + asyncpg），替身必须是协程。
    async def fake_paginated(self, **kwargs):
        if raises:
            raise RuntimeError("db down")
        return paginated

    monkeypatch.setattr(user_api_mod.UserCenterCRUD, "get_users_paginated", fake_paginated)

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(user_api_mod.user_router, prefix="/api/user")
    app.dependency_overrides[get_async_db] = lambda: None
    app.dependency_overrides[simple_auth.get_current_user] = _super_user
    return app


def test_user_table_requires_auth():
    """安全回归：无 Authorization 头访问受保护端点应被拒（鉴权落在端点依赖，不靠中间件）。

    历史上曾有一个 exclude_paths 含 '/' 的中间件形同虚设，导致部分端点裸奔；阶段七删中间件、
    鉴权全部显式落到端点依赖后，此用例守住"无 token 必拒"。
    """
    import app.user.user_api as user_api_mod

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(user_api_mod.user_router, prefix="/api/user")
    client = TestClient(app)
    r = client.get("/api/user/table")  # 不带 Authorization 头
    assert r.status_code in (401, 403)


def test_growth_requires_auth():
    """工具箱端点同样必须自带鉴权依赖——没有全局中间件兜底，漏挂即裸奔。"""
    import app.table.keyword.growth_api as growth_api_mod

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(growth_api_mod.growth_router, prefix="/api/keyword")
    client = TestClient(app)

    assert client.post("/api/keyword/growth/search", json={}).status_code in (401, 403)
    assert client.get("/api/keyword/growth/scope").status_code in (401, 403)


def test_growth_rejects_unlisted_sort_field(monkeypatch):
    """排序白名单之外的字段按参数不合法拒绝，不静默回落到默认排序。

    回落会让用户以为自己按某列排了序、看到的却是另一种顺序——而列表本身照常返回 200，
    没有任何地方会提示他排序没生效。
    """
    import app.table.keyword.growth_api as growth_api_mod
    from app.auth.simple_auth import simple_auth
    from database import get_async_db

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(growth_api_mod.growth_router, prefix="/api/keyword")
    app.dependency_overrides[get_async_db] = lambda: None
    app.dependency_overrides[simple_auth.get_current_user] = _super_user
    client = TestClient(app)

    r = client.post("/api/keyword/growth/search", json={"orderBy": "keyword"})

    # 422：白名单挡在 schema 的 Literal 上，走的是参数校验这一路（与全站一致）
    assert r.status_code == 422
    body = r.json()
    assert body["success"] is False
    assert [f["field"] for f in body["fields"]] == ["orderBy"]


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


def test_user_table_endpoint_failure_is_not_http_200(monkeypatch):
    """列表失败走非 2xx + 统一错误体，不再混在 200 里。"""
    client = TestClient(_build_app(monkeypatch, raises=True), raise_server_exceptions=False)
    r = client.get("/api/user/table")

    assert r.status_code == 500
    body = r.json()
    assert body["success"] is False
    assert body["code"] == "internal.unhandled"
    assert "request_id" in body
    # 兜底文案，不透传 "db down"
    assert "db down" not in r.text
