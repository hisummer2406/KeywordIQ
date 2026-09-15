"""请求上下文：贯穿一次请求的 request id。

用 ``contextvars`` 而非逐层传参：错误往往发生在 service 层深处，把 request id 透传到每个
函数签名里会污染整个调用链。``ContextVar`` 在 FastAPI 的 async 端点与线程池执行的同步端点
两种上下文下都能正确隔离。

中间件设置 contextvar 的时机在 ``call_next`` **之前**，因此下游（路由、依赖、异常处理器）
都能读到；反向不成立（下游设置的 contextvar 传不回中间件），本模块不依赖那个方向。
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

#: 入站请求携带该头时沿用其值，保证跨层（nginx → 应用）可串联
REQUEST_ID_HEADER = "X-Request-Id"


def new_request_id() -> str:
    """生成一个短 id。8 位十六进制足够人工在日志里比对，不必用完整 uuid。"""
    return uuid.uuid4().hex[:8]


def set_request_id(value: str):
    """设置当前上下文的 request id，返回 token 供中间件重置。"""
    return _request_id.set(value)


def reset_request_id(token) -> None:
    _request_id.reset(token)


def get_request_id() -> str | None:
    """当前请求的 id；不在请求上下文中（如后台任务、启动期）时为 None。"""
    return _request_id.get()


class RequestIdMiddleware:
    """纯 ASGI 中间件：为每个请求建立 request id，并写入响应头。

    写成纯 ASGI 而非 ``BaseHTTPMiddleware``：后者把下游放进独立任务运行，contextvar 的
    传播方向受限且额外开销可观；本中间件只需在调用下游前 set、发送响应头时注入，纯 ASGI
    最直接。

    入站已带 ``X-Request-Id`` 时沿用（nginx / 上游注入），保证跨层可串联。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = _header_value(scope.get("headers") or [], REQUEST_ID_HEADER)
        request_id = incoming or new_request_id()
        token = set_request_id(request_id)

        async def send_with_header(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append(
                    (REQUEST_ID_HEADER.lower().encode("latin-1"), request_id.encode("latin-1"))
                )
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        except Exception:
            # 不在异常路径 reset：裸 Exception（未被 APIError/RequestValidationError/
            # StarletteHTTPException/SQLAlchemyError 截住）会被 Starlette 拆到最外层的
            # ServerErrorMiddleware，在本中间件之外——它调用 error_handlers.py 的兜底
            # 处理器时要靠 get_request_id() 把编号写进响应体与日志。这里若照常 reset，
            # contextvar 会在那次真正构造 500 响应之前就被清空，让这批"最该被追踪"的
            # 失败响应体里 request_id 恒为 null、日志里恒为 "-"（Codex 复审 PR #98 P2）。
            # 异常路径下这个请求对应的 Task 不会再处理别的请求，残留的值随 Task 结束
            # 一起回收，不会泄漏进下一个请求；`app/common/error_handlers.py` 的
            # `unhandled_exception_handler` 额外显式把它写回响应头，因为那个响应绕过了
            # 下面 `send_with_header` 的包装（见该函数注释）。
            raise
        else:
            reset_request_id(token)


def _header_value(headers, name: str) -> str | None:
    """从 ASGI 的 headers 列表里取一个头（大小写不敏感）。"""
    target = name.lower().encode("latin-1")
    for key, value in headers:
        if key.lower() == target:
            decoded = value.decode("latin-1").strip()
            # 只接受可安全回写进响应头的短值，挡住上游注入超长/含控制字符的内容
            if decoded and len(decoded) <= 64 and decoded.isprintable():
                return decoded
    return None


class RequestIdLogFilter(logging.Filter):
    """给每条日志记录补上 ``request_id`` 字段，供格式串引用。

    不在请求上下文中（后台任务、启动期）时填 ``-``，避免格式化报错。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        return True
