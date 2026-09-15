"""RequestLoggingMiddleware：三种结束形态 + 不含 query string + /health 静默。

用最小的手写 ASGI app 模拟各种下游行为，不需要起完整 FastAPI 应用——中间件只关心
它包装的 send() 收到了哪些 ASGI 消息，以及 self.app() 是否抛出异常。
"""
from __future__ import annotations

import logging

import pytest

from app.common.request_logging import RequestLoggingMiddleware


def _scope(method: str = "GET", path: str = "/api/analysis/search", query_string: bytes = b"") -> dict:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query_string,
        "headers": [],
    }


async def _run(app_coro, scope):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    middleware = RequestLoggingMiddleware(app_coro)
    await middleware(scope, receive, send)
    return sent


@pytest.mark.asyncio
async def test_completed_outcome_logged_at_info(caplog):
    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    with caplog.at_level(logging.INFO, logger="app.common.request_logging"):
        await _run(downstream, _scope())

    records = [r for r in caplog.records if "请求摘要" in r.message]
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
    assert "outcome=completed" in records[0].message
    assert "status=200" in records[0].message


@pytest.mark.asyncio
async def test_query_string_not_included_in_summary(caplog):
    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    scope = _scope(path="/api/analysis/search", query_string=b"keyword=secret+term")
    with caplog.at_level(logging.INFO, logger="app.common.request_logging"):
        await _run(downstream, scope)

    records = [r for r in caplog.records if "请求摘要" in r.message]
    assert len(records) == 1
    assert "secret" not in records[0].message
    assert "keyword" not in records[0].message


@pytest.mark.asyncio
async def test_interrupted_outcome_when_body_never_completes_without_exception(caplog):
    """模拟 StreamingResponse 检测到客户端断连时的行为：响应头已发、body 只发了
    一条 more_body=True 的消息，随后协程被取消但没有异常传播到中间件——这里直接
    模拟"没有发收尾消息就返回"，不依赖真实的 anyio 任务组取消细节。"""

    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"partial-chunk", "more_body": True})
        # 正常情况下这里还会再发一条 more_body=False 的收尾消息；断连场景下不会。

    with caplog.at_level(logging.INFO, logger="app.common.request_logging"):
        await _run(downstream, _scope(path="/api/analysis/export"))

    records = [r for r in caplog.records if "请求摘要" in r.message]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert "outcome=interrupted" in records[0].message


@pytest.mark.asyncio
async def test_error_outcome_after_response_started_reraises(caplog):
    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise RuntimeError("导出生成器中途报错")

    with caplog.at_level(logging.INFO, logger="app.common.request_logging"):
        with pytest.raises(RuntimeError):
            await _run(downstream, _scope(path="/api/analysis/export"))

    records = [r for r in caplog.records if "请求摘要" in r.message]
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    assert "outcome=error" in records[0].message
    assert "outcome=error_before_response" not in records[0].message


@pytest.mark.asyncio
async def test_error_before_response_started_reraises(caplog):
    async def downstream(scope, receive, send):
        raise RuntimeError("响应头发出前就炸了")

    with caplog.at_level(logging.INFO, logger="app.common.request_logging"):
        with pytest.raises(RuntimeError):
            await _run(downstream, _scope())

    records = [r for r in caplog.records if "请求摘要" in r.message]
    assert len(records) == 1
    assert "outcome=error_before_response" in records[0].message


@pytest.mark.asyncio
async def test_health_path_produces_no_summary(caplog):
    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    with caplog.at_level(logging.INFO, logger="app.common.request_logging"):
        await _run(downstream, _scope(path="/health"))

    records = [r for r in caplog.records if "请求摘要" in r.message]
    assert len(records) == 0


@pytest.mark.asyncio
async def test_logging_failure_does_not_break_the_response(caplog):
    """日志故障不得改写业务响应：这里让请求摘要实际写入的目标流在 write() 时抛出
    （模拟磁盘写满/文件描述符失效），验证请求仍然正常走完。

    用真实的 ``StreamHandler``（``WatchedFileHandler`` 的父类之一）而不是自定义
    ``Handler`` 子类——真正的兜底发生在 ``StreamHandler.emit()`` 内部的
    try/except（捕获后调用 ``handleError`` 打印到 stderr、不向上抛），这是标准库
    handler 自带的行为，不是本中间件实现了什么特殊处理。这里验证的是我们没有
    意外绕开这条标准库保证。
    """

    class _ExplodingStream:
        def write(self, _msg):
            raise OSError("磁盘写满了")

        def flush(self):
            pass

    target_logger = logging.getLogger("app.common.request_logging")
    handler = logging.StreamHandler(_ExplodingStream())
    target_logger.addHandler(handler)
    sent_bodies = []

    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    try:
        with caplog.at_level(logging.INFO, logger="app.common.request_logging"):
            sent = await _run(downstream, _scope())
        sent_bodies = [m for m in sent if m["type"] == "http.response.body"]
    finally:
        target_logger.removeHandler(handler)

    assert len(sent_bodies) == 1
    assert sent_bodies[0]["body"] == b"ok"


@pytest.mark.asyncio
async def test_non_http_scope_passed_through_without_logging(caplog):
    called = False

    async def downstream(scope, receive, send):
        nonlocal called
        called = True

    with caplog.at_level(logging.INFO, logger="app.common.request_logging"):
        middleware = RequestLoggingMiddleware(downstream)
        await middleware({"type": "lifespan"}, None, None)

    assert called is True
    assert not any("请求摘要" in r.message for r in caplog.records)
