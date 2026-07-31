"""请求上下文：贯穿一次请求的 request id。

用 ``contextvars`` 而非逐层传参：错误往往发生在 service 层深处，把 request id 透传到每个
函数签名里会污染整个调用链。``ContextVar`` 在 FastAPI 的 async 端点与线程池执行的同步端点
两种上下文下都能正确隔离。

中间件在 unify-api-error-contract 任务组 1 落地；在那之前 :func:`get_request_id` 返回 None，
错误体里的 ``request_id`` 为 null——形状先定下来，值随后补齐。
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import Optional

_request_id: ContextVar[Optional[str]] = ContextVar("request_id", default=None)

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


def get_request_id() -> Optional[str]:
    """当前请求的 id；不在请求上下文中（如后台任务、启动期）时为 None。"""
    return _request_id.get()
