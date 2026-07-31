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


# ---------------- 五类处理器（任务组 1） ----------------


@pytest.fixture()
def probe_app(client):
    """在真实 app 上临时挂几个专门抛异常的探针路由，用后即摘。"""
    from fastapi import APIRouter
    from sqlalchemy.exc import OperationalError, ProgrammingError

    from app.common.errors import ConflictError, ServiceUnavailableError

    router = APIRouter(prefix="/__probe")

    @router.get("/api-error-4xx")
    def _conflict():
        raise ConflictError("用户名已存在", code="user.duplicate_name")

    @router.get("/api-error-5xx")
    def _unavailable():
        raise ServiceUnavailableError()

    @router.get("/db-operational")
    def _db_conn():
        raise OperationalError("SELECT 1 FROM analysis.dim_keyword", {}, Exception("conn refused"))

    @router.get("/db-other")
    def _db_other():
        raise ProgrammingError("SELECT bad FROM analysis.dim_keyword", {}, Exception("boom"))

    @router.get("/unhandled")
    def _boom():
        raise RuntimeError("SELECT * FROM analysis.dim_keyword; /etc/passwd; sk-secret")

    main.app.include_router(router)
    try:
        yield client
    finally:
        main.app.router.routes = [
            r for r in main.app.router.routes
            if not getattr(r, "path", "").startswith("/__probe")
        ]


def _assert_unified(body: dict, *, code: str):
    assert body["success"] is False
    assert body["code"] == code
    assert isinstance(body["message"], str) and body["message"]
    assert "request_id" in body


def test_api_error_maps_to_declared_status(probe_app):
    r = probe_app.get("/__probe/api-error-4xx")
    assert r.status_code == 409
    _assert_unified(r.json(), code="user.duplicate_name")
    assert r.json()["message"] == "用户名已存在"


def test_service_unavailable_is_503(probe_app):
    r = probe_app.get("/__probe/api-error-5xx")
    assert r.status_code == 503
    _assert_unified(r.json(), code="service.unavailable")


def test_operational_error_is_503_not_500(probe_app):
    """连接/超时类与业务 500 分开——否则告警策略无法分别设定。"""
    r = probe_app.get("/__probe/db-operational")
    assert r.status_code == 503
    _assert_unified(r.json(), code="db.unavailable")


def test_other_sqlalchemy_error_is_500(probe_app):
    r = probe_app.get("/__probe/db-other")
    assert r.status_code == 500
    _assert_unified(r.json(), code="db.error")


def test_db_errors_do_not_leak_sql(probe_app):
    for path in ("/__probe/db-operational", "/__probe/db-other"):
        r = probe_app.get(path)
        assert "dim_keyword" not in r.text
        assert "SELECT" not in r.text


def test_unhandled_exception_is_json_not_plaintext(probe_app):
    """此前未捕获异常返回的是 text/plain 'Internal Server Error'，连 JSON 都不是。"""
    r = probe_app.get("/__probe/unhandled")
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/json")
    _assert_unified(r.json(), code="internal.unhandled")


def test_unhandled_exception_does_not_leak_internals(probe_app):
    r = probe_app.get("/__probe/unhandled")
    for secret in ("dim_keyword", "/etc/passwd", "sk-secret", "Traceback", "RuntimeError"):
        assert secret not in r.text


def test_unhandled_exception_is_logged_with_stack(probe_app, caplog):
    """未捕获异常必须进应用日志且带栈——此前只到 uvicorn 的 stderr。"""
    import logging

    with caplog.at_level(logging.ERROR, logger="app.common.error_handlers"):
        probe_app.get("/__probe/unhandled")

    record = next(r for r in caplog.records if r.levelno >= logging.ERROR)
    assert record.exc_info is not None  # logger.exception 带栈


def test_client_error_logged_as_warning_not_error(probe_app, caplog):
    """4xx 不该产生告警噪音。"""
    import logging

    with caplog.at_level(logging.WARNING, logger="app.common.error_handlers"):
        probe_app.get("/__probe/api-error-4xx")

    levels = {r.levelno for r in caplog.records}
    assert logging.WARNING in levels
    assert logging.ERROR not in levels


def test_auth_http_exception_gets_unified_shape(client):
    """simple_auth 依赖里抛的 HTTPException 也要统一形状（此前是 {"detail": ...}）。"""
    r = client.get("/api/analysis/scope")
    assert r.status_code in (401, 403)
    body = r.json()
    _assert_unified(body, code="auth.forbidden" if r.status_code == 403 else "auth.unauthorized")
    assert "detail" not in body


def test_404_gets_unified_shape(client):
    r = client.get("/api/definitely-not-a-route")
    assert r.status_code == 404
    _assert_unified(r.json(), code="resource.not_found")


# ---------------- request id ----------------


def test_request_id_present_in_header_and_error_body(client):
    r = client.post("/api/auth/token", json={"password": SECRET})
    assert r.headers["X-Request-Id"]
    assert r.json()["request_id"] == r.headers["X-Request-Id"]


def test_request_id_reuses_inbound_header(client):
    r = client.post(
        "/api/auth/token", json={"password": SECRET}, headers={"X-Request-Id": "abc123"}
    )
    assert r.headers["X-Request-Id"] == "abc123"
    assert r.json()["request_id"] == "abc123"


def test_request_id_rejects_untrusted_inbound_value(client):
    """上游注入超长/含控制字符的值时自行生成，不原样回写进响应头。"""
    r = client.post(
        "/api/auth/token", json={"password": SECRET}, headers={"X-Request-Id": "x" * 200}
    )
    assert r.headers["X-Request-Id"] != "x" * 200
    assert len(r.headers["X-Request-Id"]) <= 64


def test_request_id_differs_across_requests(client):
    ids = {client.get("/health").headers.get("X-Request-Id") for _ in range(3)}
    assert len(ids) == 3


def test_request_id_on_success_response(client):
    r = client.get("/api/definitely-not-a-route")
    assert "X-Request-Id" in r.headers


# ---------------- 逐端点契约一致性（任务 3.7） ----------------

def _as_super():
    class _Super:
        is_super = True
        permission_codes: set = set()
    return _Super()


@pytest.fixture()
def su_client(client):
    """以超管身份访问，用于触达鉴权之后的业务失败。"""
    from app.auth.simple_auth import simple_auth
    main.app.dependency_overrides[simple_auth.get_current_user] = _as_super
    try:
        yield client
    finally:
        main.app.dependency_overrides.clear()


def test_all_failure_shapes_share_the_same_keys(client):
    """四类失败（校验/鉴权/业务/未找到）的响应体字段名与层级完全一致。

    这是本变更的核心目标：此前 detail 在业务错误里是字符串、在校验错误里是数组、
    未捕获异常甚至返回 text/plain，前端无法用一套代码解析。
    """
    # 三类必须都用未覆盖鉴权的干净 client，否则"无 token"那条会被依赖覆盖放行
    cases = [
        client.post("/api/auth/token", json={"password": "x"}),   # 校验 422
        client.get("/api/definitely-not-a-route"),                # 404
        client.get("/api/user/table"),                            # 鉴权 403
    ]

    required = {"success", "code", "message", "request_id"}
    for r in cases:
        assert r.status_code >= 400
        assert r.headers["content-type"].startswith("application/json")
        body = r.json()
        assert required <= set(body.keys()), (r.request.url, body)
        assert body["success"] is False
        assert "detail" not in body
        assert "status" not in body and "msg" not in body   # amis 信封不得残留


def test_upload_bad_filename_is_400_not_200(su_client):
    """上传侧的业务失败不再是 HTTP 200 + status:1。"""
    r = su_client.post(
        "/api/upload/oss/upload-credentials",
        json={"site": "US", "filename": "not-a-csv.txt"},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "upload.bad_file"
    assert "status" not in body


def test_upload_unparseable_date_is_400(su_client):
    r = su_client.post(
        "/api/upload/oss/upload-credentials",
        json={"site": "US", "filename": "US_Day_no_date.csv"},
    )
    assert r.status_code == 400
    assert r.json()["code"] == "upload.bad_file"


def test_analysis_unknown_site_is_422_with_domain_code(su_client):
    """作用域错误带域内错误码，不被路由层盖成通用码。"""
    r = su_client.post("/api/analysis/search", json={"site": "UK", "view": "DAY"})
    assert r.status_code == 422
    assert r.json()["code"] == "analysis.scope_invalid"


def test_missing_permission_is_403_with_auth_code(client):
    """有 token 但缺权限 → 403 + auth.forbidden。"""
    from app.auth.simple_auth import simple_auth

    class _ViewOnly:
        is_super = False
        permission_codes = {"analysis:view"}

    main.app.dependency_overrides[simple_auth.get_current_user] = lambda: _ViewOnly()
    try:
        r = client.post("/api/analysis/export", json={"site": "US", "view": "DAY"})
        assert r.status_code == 403
        assert r.json()["code"] == "auth.forbidden"
    finally:
        main.app.dependency_overrides.clear()


# ---------------- 后台任务的独立错误路径（任务 4.5） ----------------

def test_background_import_task_logs_stack_and_does_not_escape(monkeypatch, caplog):
    """全局处理器不拦截 BackgroundTasks，故后台任务必须自捕获且带栈记录。

    此前写的是 logger.error(..., exc)，栈永久丢失——而 GB 级导入失败时栈往往是
    唯一线索。
    """
    import logging

    from datetime import date

    import app.table.upload.upload_api as upload_api

    def _boom(**kwargs):
        raise RuntimeError("COPY failed at row 1234567")

    monkeypatch.setattr(upload_api, "run_import", _boom)

    with caplog.at_level(logging.ERROR, logger="app.table.upload.upload_api"):
        # 不应抛出——异常必须止步于后台任务
        upload_api._run_import_bg("/tmp/x.csv", "US_Day_2026_07_18.csv", "US", "DAY", date(2026, 7, 18))

    record = next(r for r in caplog.records if "导入后台任务异常" in r.getMessage())
    assert record.exc_info is not None          # logger.exception 带栈
    assert record.levelno == logging.ERROR
