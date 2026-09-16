"""进程内多组轮询循环，不引入 APScheduler 等新依赖（design.md D1）。

每组任务用一个常驻 `asyncio` 循环表达，触发判据是"距上次成功执行的时间"
（查 `ops_task` 里该 `kind` 最近一次 `SUCCEEDED` 的 `finished_at`），不是进程内存里的
下次触发时间戳——这样服务重启不会错过或重复触发同一轮（例如 09:00 日报，重启在
09:03 发生仍会补发；重启在 08:58 发生不会额外多发一次）。

轮询粒度统一取 `min(interval_seconds, 60)`：给 daily 这类长间隔任务最多引入 60 秒
的触发延迟，换来一份实现（不必为"多久判一次"单独再调一个参数）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from ops_bot import crud
from ops_bot.database import OpsAsyncSessionFactory

logger = logging.getLogger(__name__)

JobFn = Callable[[], Awaitable[str]]
"""巡检/调度任务的执行函数：无参数，返回一段供审计/人工核对的输出摘要；抛异常即失败。"""

_POLL_GRANULARITY_SECONDS = 60


@dataclass(frozen=True)
class ScheduledJob:
    kind: str
    interval_seconds: int
    run: JobFn


async def _is_due(kind: str, interval_seconds: int) -> bool:
    async with OpsAsyncSessionFactory() as session:
        last = await crud.last_succeeded_at(session, kind)
    if last is None:
        return True
    return (datetime.now(UTC) - last).total_seconds() >= interval_seconds


async def _run_once(job: ScheduledJob) -> None:
    async with OpsAsyncSessionFactory() as session:
        task_id = await crud.create_task(session, kind=job.kind)
        await session.commit()

    try:
        output = await job.run()
    except Exception:
        logger.exception("调度任务 %s 执行失败", job.kind)
        async with OpsAsyncSessionFactory() as session:
            await crud.mark_task_failed(session, task_id, output="执行异常，详见应用日志")
            await session.commit()
        return

    async with OpsAsyncSessionFactory() as session:
        await crud.mark_task_succeeded(session, task_id, output=output)
        await session.commit()


async def _loop(job: ScheduledJob) -> None:
    sleep_seconds = min(job.interval_seconds, _POLL_GRANULARITY_SECONDS)
    while True:
        try:
            if await _is_due(job.kind, job.interval_seconds):
                await _run_once(job)
        except asyncio.CancelledError:
            raise
        except Exception:
            # 判定本身失败（如数据库暂时不可达）不该杀死整条循环——下一轮再试。
            logger.exception("调度循环 %s 的判定阶段异常", job.kind)
        await asyncio.sleep(sleep_seconds)


def start(jobs: list[ScheduledJob]) -> list[asyncio.Task]:
    """按传入的任务列表各起一个常驻循环，返回 task 句柄供 lifespan 收尾时 cancel。"""
    return [asyncio.create_task(_loop(job), name=f"ops-scheduler-{job.kind}") for job in jobs]


async def stop(tasks: list[asyncio.Task]) -> None:
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
