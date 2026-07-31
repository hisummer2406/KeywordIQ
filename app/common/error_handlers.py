"""全局异常处理器：所有失败响应的唯一出口。

契约（见 openspec/changes/unify-api-error-contract/specs/api-error-contract/spec.md）::

    {"success": false, "code": "<域>.<原因>", "message": "<面向用户的中文>", "request_id": "..."}

业务失败 MUST NOT 以 HTTP 200 返回；内部细节（异常栈、SQL、路径、object key）只进日志。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.common.request_context import get_request_id

logger = logging.getLogger(__name__)

#: 未预期错误对外的固定文案。原始异常字符串可能含 SQL、路径、凭证，绝不透传。
UNHANDLED_MESSAGE = "服务器内部错误，请稍后重试"


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
    return json_error(422, "request.invalid", "请求参数不合法", fields=fields)


def register_error_handlers(app: FastAPI) -> None:
    """把处理器挂到应用上。在 main.py 构造 app 之后调用。"""
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
