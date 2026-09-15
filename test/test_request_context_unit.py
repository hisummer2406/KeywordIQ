"""RequestIdMiddleware：入站头校验保持不变的回归测试。

design.md 决策 C 把 nginx 定为 request id 的唯一权威生成者，应用侧不再是被外部任意
调用方直接命中的入口，但 `_header_value` 现有的长度/可打印字符校验仍然保留作为纵深
防御（本地开发不经 nginx 直连 8000 时、以及万一 nginx 配置被绕过时）。这里固化"不需要
改"这个结论，避免日后有人在改这条中间件时把校验当成已经不需要的旧代码删掉。
"""
from __future__ import annotations

from app.common.request_context import (
    REQUEST_ID_HEADER,
    RequestIdMiddleware,
    get_request_id,
)


def _scope(headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {"type": "http", "headers": headers or []}


async def _run_and_capture_id(scope: dict) -> tuple[str | None, list[dict]]:
    sent = []
    seen_id_inside_app = {}

    async def downstream(scope, receive, send):
        seen_id_inside_app["value"] = get_request_id()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    middleware = RequestIdMiddleware(downstream)
    await middleware(scope, receive, send)
    return seen_id_inside_app["value"], sent


def _response_header(sent: list[dict], name: bytes) -> bytes | None:
    start = next(m for m in sent if m["type"] == "http.response.start")
    for key, value in start["headers"]:
        if key.lower() == name.lower():
            return value
    return None


async def test_valid_incoming_header_is_reused():
    scope = _scope([(b"x-request-id", b"abc123")])
    seen, sent = await _run_and_capture_id(scope)
    assert seen == "abc123"
    assert _response_header(sent, REQUEST_ID_HEADER.encode()) == b"abc123"


async def test_missing_header_generates_new_id():
    seen, sent = await _run_and_capture_id(_scope())
    assert seen is not None
    assert len(seen) == 8  # new_request_id() 生成 8 位 hex
    assert _response_header(sent, REQUEST_ID_HEADER.encode()) == seen.encode()


async def test_overlong_incoming_header_is_rejected_and_regenerated():
    overlong = b"a" * 65  # 超过 64 字符上限
    seen, _ = await _run_and_capture_id(_scope([(b"x-request-id", overlong)]))
    assert seen != overlong.decode()
    assert len(seen) == 8


async def test_non_printable_incoming_header_is_rejected_and_regenerated():
    scope = _scope([(b"x-request-id", b"abc\x00123")])
    seen, _ = await _run_and_capture_id(scope)
    assert seen != "abc\x00123"
    assert len(seen) == 8


async def test_context_reset_after_request():
    """中间件退出后 contextvar 必须复位，否则同一 worker 处理下一个请求时会读到
    上一个请求的 request_id（后台任务/启动期也依赖它复位为 None）。"""
    await _run_and_capture_id(_scope([(b"x-request-id", b"leaked-id")]))
    assert get_request_id() is None


async def test_concurrent_requests_do_not_leak_request_id():
    """并发场景下 contextvar 必须按任务隔离，不能串号——否则日志里 A 请求的记录会被
    打上 B 请求的 request_id，排查时会被引向错误的调用链。

    下游处理过程中显式 ``await asyncio.sleep(0)`` 让出控制权，制造真实的并发交错窗口
    ——处理恢复后仍必须读到自己的 request_id，不能被交错期间运行的另一个任务覆盖。
    """
    import asyncio

    async def run_one(rid: str) -> str:
        seen = {}

        async def downstream(scope, receive, send):
            await asyncio.sleep(0)
            seen["before"] = get_request_id()
            await asyncio.sleep(0)
            seen["after"] = get_request_id()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(_message):
            pass

        middleware = RequestIdMiddleware(downstream)
        await middleware(_scope([(b"x-request-id", rid.encode())]), receive, send)
        return seen["before"], seen["after"]

    results = await asyncio.gather(
        run_one("req-a"), run_one("req-b"), run_one("req-c"),
    )
    assert results == [("req-a", "req-a"), ("req-b", "req-b"), ("req-c", "req-c")]
