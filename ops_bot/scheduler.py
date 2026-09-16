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
from datetime import time as dt_time
from zoneinfo import ZoneInfo

from ops_bot import crud
from ops_bot.database import OpsAsyncSessionFactory

logger = logging.getLogger(__name__)

JobFn = Callable[[], Awaitable[str]]
"""巡检/调度任务的执行函数：无参数，返回一段供审计/人工核对的输出摘要；抛异常即失败。"""

_POLL_GRANULARITY_SECONDS = 60


DueFn = Callable[[datetime | None], bool]


@dataclass(frozen=True)
class ScheduledJob:
    kind: str
    interval_seconds: int
    run: JobFn
    should_run: DueFn | None = None
    """可选的自定义判据，接收上次成功时间（可能为 `None`），返回现在是否该跑。

    不给时退化为默认的"距上次成功超过 interval_seconds"。daily 巡检摘要这类需要
    "今天 09:00 之后且今天还没成功过"的日历语义时才需要它——纯粹的"距上次成功多久"
    表达不出"09:00 之前不要提前发"这条约束。
    """


def daily_at(hour: int, minute: int = 0) -> DueFn:
    """构造一个"每日北京时间 HH:MM 之后、今天还没成功过"的 `should_run` 判据。

    `09:00 之前不要提前发`与`09:03 重启仍要补发`这两条约束单靠"距上次成功多久"
    表达不出来（前者需要日历语义，后者需要幂等语义），这个判据把两者合在一起。
    """

    def should_run(last: datetime | None) -> bool:
        now_local = datetime.now(ZoneInfo("Asia/Shanghai"))
        if now_local.time() < dt_time(hour, minute):
            return False
        if last is None:
            return True
        last_local = last.astimezone(ZoneInfo("Asia/Shanghai"))
        return last_local.date() < now_local.date()

    return should_run


async def _is_due(job: ScheduledJob) -> bool:
    async with OpsAsyncSessionFactory() as session:
        last = await crud.last_succeeded_at(session, job.kind)
    if job.should_run is not None:
        return job.should_run(last)
    if last is None:
        return True
    return (datetime.now(UTC) - last).total_seconds() >= job.interval_seconds


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
            if await _is_due(job):
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
