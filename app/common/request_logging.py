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
    - ``error``：``self.app()`` 抛出的异常传播到了这里，且响应头已经发出——如导出
      生成器中途报错。
    - ``error_before_response``：异常传播到这里时响应头还没发出。**这不代表客户端
      什么都没收到**：`app/common/error_handlers.py` 给裸 ``Exception`` 注册的兜底
      处理器（``unhandled_exception_handler``）会被 Starlette 拆到最外层的
      ``ServerErrorMiddleware`` 里（凡是注册给 `Exception`/500 的处理器都会被
      ``Starlette.build_middleware_stack`` 挪到那一层，而不是留在更靠内、与
      ``APIError``/``RequestValidationError``/``StarletteHTTPException``/
      ``SQLAlchemyError`` 那四个处理器同层的 ``ExceptionMiddleware``），那一层在
      本中间件（乃至全部 ``add_middleware`` 注册的中间件）之外——异常能传播到这里，
      说明它没被那四个"挂在 ExceptionMiddleware 上"的处理器截住，因此必然会被
      ``ServerErrorMiddleware`` 外层的兜底处理器转成 500 发给客户端，只是这里的
      ``send`` 包装看不到那次发送。``status`` 因此显式记 500，不留 ``None``——
      ``None`` 会被读成"连响应都没发"，而客户端实际收到的是确定的 500
      （Codex 复审 PR #98 P2）。这个假设耦合着 error_handlers.py 仍然给 `Exception`
      注册兜底处理器；那条注册若被移除，这里要一并回来重新核实。
    两种都记录、都原样重新抛出，不吞异常。
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
            if state["response_started"]:
                outcome = "error"
                status = state["status"]
            else:
                # 见类 docstring：这里看不到发送动作，不代表客户端最终没收到响应——
                # 兜底的 Exception 处理器在 ServerErrorMiddleware 里，必然会把它转成
                # 500。显式记 500 而不是 None，避免被读成"连响应都没发"。
                outcome = "error_before_response"
                status = 500
            logger.exception(
                "请求摘要 %s %s status=%s duration_ms=%d outcome=%s request_id=%s",
                method, path, status, duration_ms, outcome, get_request_id(),
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
