"""全局异常处理器：所有失败响应的唯一出口。

契约（见 openspec/changes/unify-api-error-contract/specs/api-error-contract/spec.md）::

    {"success": false, "code": "<域>.<原因>", "message": "<面向用户的中文>", "request_id": "..."}

业务失败 MUST NOT 以 HTTP 200 返回；内部细节（异常栈、SQL、路径、object key）只进日志。

**日志级别由 handler 分工自然完成**（design 决策 11）：4xx 类走 ``logger.warning``（不该产生
告警噪音），5xx 类走 ``logger.exception``（自带完整栈，是接入告警的前置）。不引入"先判状态码
再决定级别"的集中逻辑——handler 已经按类别分好，再判一次是重复。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.common import errors
from app.common.errors import APIError
from app.common.request_context import get_request_id

logger = logging.getLogger(__name__)

#: 未预期错误对外的固定文案。原始异常字符串可能含 SQL、路径、凭证，绝不透传。
UNHANDLED_MESSAGE = "服务器内部错误，请稍后重试"

#: Starlette 内建异常的状态码 → 错误码。未列出的按状态码段兜底。
_STATUS_CODE_MAP = {
    400: errors.CODE_REQUEST_INVALID,
    401: errors.CODE_AUTH_UNAUTHORIZED,
    403: errors.CODE_AUTH_FORBIDDEN,
    404: errors.CODE_RESOURCE_NOT_FOUND,
    405: "request.method_not_allowed",
    409: errors.CODE_RESOURCE_CONFLICT,
    413: errors.CODE_REQUEST_TOO_LARGE,
    422: errors.CODE_REQUEST_UNPROCESSABLE,
    429: "request.rate_limited",
}


def error_body(code: str, message: str, **extra: Any) -> dict:
    """统一失败响应体。``success`` 与非 2xx 状态码有冗余，但让响应体自解释——
    拿到孤立的一段 JSON（日志、抓包）也能判断成败。"""
    return {
        "success": False,
        "code": code,
        "message": message,
        "request_id": get_request_id(),
        **extra,
    }


def json_error(status_code: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=error_body(code, message, **extra))


def _field_path(loc: tuple) -> str:
    """Pydantic 的 loc 形如 ``("body", "user", "name")``；去掉首段的 body/query/path 前缀。"""
    parts = loc[1:] if loc and loc[0] in ("body", "query", "path", "header", "cookie") else loc
    return ".".join(str(p) for p in parts)


# ---------------- 各类处理器 ----------------


async def api_error_handler(request, exc: APIError) -> JSONResponse:
    """业务异常 → 其声明的状态码与错误码。

    4xx 是客户端问题，WARNING 级、不带栈、不该触发告警；极少数 5xx 的 APIError 才带栈。
    """
    if exc.http_status >= 500:
        logger.exception("业务异常(5xx) %s %s: %s", request.method, request.url.path, exc.code)
    else:
        logger.warning(
            "业务异常 %s %s: %s %s", request.method, request.url.path, exc.code, exc.message
        )
    return json_error(exc.http_status, exc.code, exc.message)


async def validation_exception_handler(request, exc: RequestValidationError) -> JSONResponse:
    """参数校验失败 → 422。

    **白名单重建**，而不是从 ``exc.errors()`` 里过滤掉敏感键：

    - Pydantic v2 的错误对象带 ``input``，且在 ``missing`` 类型下 ``input`` 是**整个父对象**，
      即客户端提交的完整请求体。直接透传等于把密码写进响应体与 nginx access log
      （实测：向 /api/auth/token 提交 {"password": ...} 且缺 username 时明文回显）。
    - 黑名单过滤在 Pydantic 升级新增字段时会重新泄漏；白名单只有显式列出的才出得去。
    - ``exc.errors(include_input=False)`` 也能去掉 input，但去不掉 ``url``，且把「是否泄漏」
      寄托在一个容易在重构中被漏传的参数上。
    """
    fields = [
        {"field": _field_path(tuple(e.get("loc", ()))), "msg": e.get("msg", "")}
        for e in exc.errors()
    ]
    logger.warning("请求参数校验失败 %s %s: %s", request.method, request.url.path, fields)
    return json_error(422, errors.CODE_REQUEST_INVALID, "请求参数不合法", fields=fields)


async def http_exception_handler(request, exc: StarletteHTTPException) -> JSONResponse:
    """框架与依赖里抛出的 ``HTTPException`` → 统一形状。

    覆盖 FastAPI 自身的 404/405、以及 `simple_auth` 依赖里抛的 401/403——那几处是
    FastAPI 的惯用法，保留 `raise HTTPException` 没问题，但响应形状必须与其他失败一致。
    """
    code = _STATUS_CODE_MAP.get(exc.status_code)
    if code is None:
        code = errors.CODE_INTERNAL_UNHANDLED if exc.status_code >= 500 else "request.error"
    message = exc.detail if isinstance(exc.detail, str) and exc.detail else "请求失败"

    if exc.status_code >= 500:
        logger.exception("框架异常(5xx) %s %s", request.method, request.url.path)
    else:
        logger.warning(
            "框架异常 %s %s: %s %s", request.method, request.url.path, exc.status_code, message
        )
    headers = getattr(exc, "headers", None)
    response = json_error(exc.status_code, code, message)
    if headers:
        response.headers.update(headers)  # 保留 WWW-Authenticate 等鉴权语义头
    return response


async def sqlalchemy_exception_handler(request, exc: SQLAlchemyError) -> JSONResponse:
    """数据库异常 → 与业务 500 分开（design 决策 10）。

    ``OperationalError`` 是连接/超时/池耗尽一类，语义上是「服务暂时不可用」，客户端重试有意义
    → 503。其余（约束冲突、编程错误等）是服务端缺陷 → 500。

    分开的实际意义：已知的「导入期半开连接把 worker 挂死」正属前者，混在业务 500 里就等于
    失去了最该被盯住的信号。
    """
    if isinstance(exc, OperationalError):
        logger.exception("数据库连接异常 %s %s", request.method, request.url.path)
        return json_error(503, errors.CODE_DB_UNAVAILABLE, "服务暂时不可用，请稍后重试")
    logger.exception("数据库异常 %s %s", request.method, request.url.path)
    return json_error(500, errors.CODE_DB_ERROR, UNHANDLED_MESSAGE)


async def unhandled_exception_handler(request, exc: Exception) -> JSONResponse:
    """兜底 → 500，固定文案。

    原始异常字符串可能含 SQL、文件路径、object key 甚至凭证，**绝不进响应体**；
    完整栈以 ERROR 级进 ``logs/app.log``（此前未捕获异常只到 uvicorn 的 stderr，
    应用日志里没有任何痕迹）。
    """
    logger.exception("未捕获异常 %s %s", request.method, request.url.path)
    return json_error(500, errors.CODE_INTERNAL_UNHANDLED, UNHANDLED_MESSAGE)


def register_error_handlers(app: FastAPI) -> None:
    """把处理器挂到应用上。在 main.py 构造 app 之后调用。

    注册顺序不影响匹配（Starlette 按异常类型精确/继承查找），但保持从具体到兜底的书写顺序
    便于阅读。
    """
    app.add_exception_handler(APIError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
