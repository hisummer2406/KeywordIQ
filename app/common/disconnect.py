"""客户端断开时取消请求处理（#154，async-request-execution「客户端断开或请求被取消时数据库侧停止执行」）。

Starlette 0.27 + 纯 ASGI 中间件下，客户端断开不会取消处理函数：查询照跑到完整请求预算，
池里的连接也一直被占着。这里把处理协程放进子任务，同时在 ``receive()`` 上等
``http.disconnect``，先到者胜。

**取消只让应用放弃，数据库停下靠引擎配置**：SQLAlchemy 在任务取消时作废该连接，数据库按
``client_connection_check_interval`` 轮询套接字发现客户端已断才中止语句（``database.py``）。
两者缺一不可——没有轮询，语句照跑到 ``statement_timeout``（design D2「机制验证结果」）。

断开后返回 499（nginx「客户端关闭请求」的约定）。客户端已经收不到任何响应，uvicorn 对已断
连接的 ``send`` 直接忽略；返回一个响应而不是抛异常，是为了不在 uvicorn 那层留下
「Exception in ASGI application」的错误栈——断开是正常现象，不是故障。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

from starlette.requests import Request
from starlette.responses import Response

T = TypeVar("T")

CLIENT_CLOSED_REQUEST = 499


async def _wait_for_disconnect(request: Request) -> None:
    # 请求体此前已被 FastAPI 读完，之后的 receive() 只会在断开（或响应发完）时返回；
    # 防御性地跳过可能出现的空 http.request 消息。
    while True:
        message = await request.receive()
        if message["type"] == "http.disconnect":
            return


async def cancel_on_disconnect(request: Request, work: Awaitable[T]) -> T | Response:
    """执行 ``work``；客户端先断开则取消它并返回 499。

    ``work`` 的异常原样传播。外层被取消（如优雅关闭）时子任务一并取消，不留孤儿任务。
    """
    task = asyncio.ensure_future(work)
    watcher = asyncio.ensure_future(_wait_for_disconnect(request))
    try:
        await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        # 正常完成、断开、外层取消三种情况都走这里：没结束的一律取消并等它真正退出，
        # 让查询任务里的 finally（释放并发位、还原截止时刻、作废连接）在返回前跑完。
        for pending in (task, watcher):
            if not pending.done():
                pending.cancel()
        await asyncio.gather(task, watcher, return_exceptions=True)
    if task.cancelled():
        return Response(status_code=CLIENT_CLOSED_REQUEST)
    return task.result()
