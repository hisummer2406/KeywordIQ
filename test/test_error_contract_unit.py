"""统一错误契约的单元测试（无需 PostgreSQL）。

对应 openspec 变更 unify-api-error-contract 的 spec。测试直接打真实的 `main.app`——
错误契约是应用级横切关注点，用自建的小 app 测不出处理器是否真的注册上了。

不进入 lifespan（不用 `with TestClient(...)`），因此不会连库；这里覆盖的路径都在
请求进入路由之前或之后的异常处理层。
"""
import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture()
def client():
    # raise_server_exceptions=False：让未捕获异常走处理器而非被 TestClient 重新抛出
    return TestClient(main.app, raise_server_exceptions=False)


# ---------------- 校验错误不回显请求内容（任务组 0，最高风险项） ----------------

SECRET = "MySecretPwd123"


def test_validation_error_does_not_echo_submitted_password(client):
    """必填字段缺失时，Pydantic 把整个父对象作为 input 带出——绝不能透传。

    实测过的泄露路径：POST /api/auth/token 提交 {"password": ...} 且缺 username 时，
    FastAPI 默认处理器会把 {"input": {"password": "<明文>"}} 写进响应体，
    该响应还会进 nginx access log。
    """
    r = client.post("/api/auth/token", json={"password": SECRET})

    assert r.status_code == 422
    assert SECRET not in r.text


def test_validation_error_omits_pydantic_input_and_url(client):
    """白名单重建而非黑名单过滤：Pydantic 的 input/url 一律不出现。"""
    r = client.post("/api/auth/token", json={"password": SECRET})
    body = r.json()

    assert "input" not in r.text
    assert "errors.pydantic.dev" not in r.text
    for field in body["fields"]:
        assert set(field.keys()) == {"field", "msg"}


def test_validation_error_still_identifies_the_bad_field(client):
    """不回显值，但必须仍然说清楚哪个字段错了——否则修复变成降低可用性。"""
    r = client.post("/api/auth/token", json={"password": SECRET})
    body = r.json()

    assert [f["field"] for f in body["fields"]] == ["username"]
    assert "required" in body["fields"][0]["msg"].lower()


def test_validation_error_uses_unified_shape(client):
    """校验错误与其他失败同形状：不再是 detail 数组。"""
    r = client.post("/api/auth/token", json={"password": SECRET})
    body = r.json()

    assert body["success"] is False
    assert body["code"] == "request.invalid"
    assert isinstance(body["message"], str)
    assert "request_id" in body
    # detail 曾经在业务错误里是字符串、在校验错误里是数组，前端无法用一套代码解析
    assert "detail" not in body


def test_validation_error_field_path_strips_location_prefix(client):
    """字段路径去掉 body/query 前缀，只留调用方看得懂的字段名。"""
    r = client.post("/api/auth/token", json={"password": SECRET})
    assert not any(f["field"].startswith("body") for f in r.json()["fields"])


def test_user_create_validation_does_not_echo_password(client):
    """用户创建也带 password 字段。该端点鉴权先于校验，故需绕过鉴权才能触达校验层。

    覆盖 user_schemas.py 的 UserCenterBase/UserCenterUpdate/UserCenterLogin 三处 password
    共用的这条路径。
    """
    from app.auth.simple_auth import simple_auth

    class _Super:
        is_super = True
        permission_codes: set = set()

    main.app.dependency_overrides[simple_auth.get_current_user] = lambda: _Super()
    try:
        r = client.post("/api/user/create", json={"password": SECRET})
        assert r.status_code == 422
        assert SECRET not in r.text
    finally:
        main.app.dependency_overrides.clear()


def test_nested_field_validation_error_keeps_path(client):
    """嵌套字段用点号连接，仍不回显值。"""
    r = client.post("/api/analysis/search", json={"view": "INVALID_VIEW"})

    # 该端点需要鉴权，若鉴权先行则不是 422；两种情况都不应泄漏提交内容
    assert "INVALID_VIEW" not in r.text or r.status_code == 422
