"""请求摘要中间件：每个请求结束时记录一条可检索的摘要。

现状（改动前）完全没有成功请求的耗时记录——`Dockerfile` 用 `--no-access-log` 关掉了
uvicorn 自带的访问日志，`app/common/error_handlers.py` 的异常处理器只在失败路径触发且
不含耗时。这里补的是"这条请求存在过、耗时多少、怎么结束的"这一层，与错误处理器记录的
"为什么失败"是互补关系，不是重复记录同一件事（design.md 决策 B）。

纯 ASGI 实现而非 ``BaseHTTPMiddleware``：与 ``RequestIdMiddleware`` 同样的理由——
后者把下游放进独立任务运行，直接包装 send 更省一次任务切换。
"""
from __future__ import annotations

import logging
import time

from app.common.request_context import get_request_id

logger = logging.getLogger(__name__)

#: 被轮询探活的路径不计入摘要噪音——nginx 侧对 /health 也是 access_log off，
#: 两处用同一个判断依据。不做更宽的"所有 GET 跳过"规则，那会把真实慢请求一并静默。
_SKIP_PATHS = frozenset({"/health"})


class RequestLoggingMiddleware:
    """必须在 ``RequestIdMiddleware`` 之前调用 ``add_middleware``（因此比它更内层）：
    本仓库"后添加的中间件在更外层"，``RequestIdMiddleware`` 要留在最外层才能保证它
    建立的 request_id 上下文对所有内层可见，这里读 ``get_request_id()`` 才有值。

    通过包装 ``send`` 观察两类 ASGI 消息来判定请求的结束形态：

    - ``http.response.start``：记录状态码，标记响应头已发出。
    - ``http.response.body``：``more_body`` 缺省或为 False 时标记 body 已发完——
      这是 ASGI 规范下常规响应与流式响应「最后一块」的共同表达方式，不需要分别处理。

    结束时按以下判据归类，只输出一条摘要（不重复记录）：

    - ``completed``：无异常、body 已发完。
    - ``interrupted``：无异常，但响应头已发出、body 未发完——覆盖 Starlette
      ``StreamingResponse`` 检测到客户端断连时的内部行为：它用任务组取消"产出响应体"
      的协程，这个取消不会作为异常传播到这里，只是最后一条 body 消息永远不会带上
      ``more_body=False``。不加这一档，所有中途断连的导出都会被误记成 completed
      （CLAUDE.md："取消/断连不能伪造正常完成"）。
    - ``error``：``self.app()`` 抛出的异常传播到了这里——说明异常发生在响应头已经
      发出之后（若在此之前，全局异常处理器已经把它转成了 JSONResponse，不会传播到
      这一层），如导出生成器中途报错。记录后原样重新抛出，不吞异常。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") in _SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "-")
        path = scope.get("path", "-")
        state = {"status": None, "response_started": False, "body_completed": False}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                state["status"] = message.get("status")
                state["response_started"] = True
            elif message["type"] == "http.response.body":
                if not message.get("more_body", False):
                    state["body_completed"] = True
            await send(message)

        started = time.monotonic()
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            duration_ms = int((time.monotonic() - started) * 1000)
            # 正常情况下异常会在响应头发出之前被全局异常处理器转成 JSONResponse，
            # 不会传播到这里；传播到这里说明要么是响应头已发出后才炸的（导出生成器
            # 中途报错），要么是响应头尚未发出就跳过了全局处理器（框架层缺陷）。
            # 两种都要记录、都原样重新抛出，只用 outcome 区分是哪一种。
            outcome = "error" if state["response_started"] else "error_before_response"
            logger.exception(
                "请求摘要 %s %s status=%s duration_ms=%d outcome=%s request_id=%s",
                method, path, state["status"], duration_ms, outcome, get_request_id(),
            )
            raise
        else:
            duration_ms = int((time.monotonic() - started) * 1000)
            if state["response_started"] and not state["body_completed"]:
                outcome = "interrupted"
                logger.warning(
                    "请求摘要 %s %s status=%s duration_ms=%d outcome=%s request_id=%s",
                    method, path, state["status"], duration_ms, outcome, get_request_id(),
                )
            else:
                outcome = "completed"
                logger.info(
                    "请求摘要 %s %s status=%s duration_ms=%d outcome=%s request_id=%s",
                    method, path, state["status"], duration_ms, outcome, get_request_id(),
                )
