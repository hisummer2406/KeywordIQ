"""中立的后台任务轮询编排。

只负责四件事：**轮询节奏、任务优先级、取消、顶层异常隔离**。不知道也不关心每个 job
内部具体做什么——是否要 ``run_in_threadpool``、内部异常怎么处理与记日志，全部由 job
自己决定。因此本模块 MUST NOT import 任何 ``app.table.*`` 领域模块。

**job 的约定只有一条**：``async () -> bool``。返回 ``True`` 表示"这一轮真的做了有意义
的工作"——``run_poller`` 会立即跳过本轮排在它之后的其余 job，从列表第一个重新开始下
一轮，不等 ``sleep``。这让积压的工作（队列里还有下一个批次、还有下一个到期站点）能够
连续处理。

**恒返回 ``False`` 的 job 是"维护性任务"**：它们每一轮都会被执行到（哪怕前一轮因为
某个工作型 job 返回 True 而被提前重跑），但从不参与"要不要整轮重来"的判定——例如回收
超时上传、清理过期预览行，这类任务的意义在于"每轮都有人回头看它"，不在于"这次是不是
处理了什么"。

只有一整轮里所有 job 都返回 ``False`` 时才会 ``sleep(interval_seconds)``。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence

logger = logging.getLogger(__name__)

PollerJob = Callable[[], Awaitable[bool]]


async def run_poller(jobs: Sequence[PollerJob], interval_seconds: float, *, name: str) -> None:
    """周期性依次执行 ``jobs``，直到被取消。

    顶层异常隔离：job 抛出的、未被自己捕获的异常在这里兜底记日志、跳过本轮剩余 job，
    但不中断轮询本身——退出之后没有任何东西会重启它。``CancelledError`` 不属于这层
    隔离的范围，原样向上抛出，好让调用方能正常取消这个任务。
    """
    logger.info("🧹 %s 轮询任务已启动（每 %s 秒一轮）", name, interval_seconds)
    while True:
        did_work = False
        try:
            for job in jobs:
                if await job():
                    did_work = True
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("%s 轮询任务本轮出错，跳过", name)
        if not did_work:
            await asyncio.sleep(interval_seconds)
