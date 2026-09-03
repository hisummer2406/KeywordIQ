"""排队批次的接手者（drainer），导入领域自己的两件"一轮工作"：排队导入接手、超时
上传回收。

导入用 `pg_try_advisory_lock` 串行化，同一时刻只跑一个。抢不到锁的批次留在 `QUEUED`
——**这两个函数就是让「排队中」名副其实的那一半**。没有它们，`QUEUED` 只是个死标签：
批次停在那里，界面显示「队列中」，而没有任何东西会回头看它，用户在等一件永远不会
发生的事（真实发生过：日报 300K 行要跑约 4 分钟，在这期间提交的第二个文件就此消失）。

**为什么是轮询而不是「导入结束时接着跑下一个」**：链式接力在进程被杀、导入崩在半路、
或前一个导入是别的 worker 跑的时候都会断链，而断链的表现恰恰又是「永远排队」——
和它要修的病一模一样。轮询多花一条 SELECT，换来的是自愈。

**多 worker 下不会重复执行**：4 个 worker 各自的轮询任务都要先拿同一把咨询锁，拿不到
的直接返回。串行约束由锁保证，与轮询任务有几个无关。

轮询节奏、任务优先级、取消与顶层异常隔离**不在这个文件里**，那是 `app/jobs/poller.py`
的职责；本文件只暴露 `reap_stale_uploads_round` / `drain_one_batch_round` 两个
`async () -> bool` 的"一轮工作"入口，由 `main.py` 的 lifespan 组装进 job 列表。
竞对领域的预览清理与独立站拉取分别在 `app/table/competitor/watch_service.py` 与
`app/table/competitor/store_service.py` 里暴露各自的入口，同样不在这里。
"""

from __future__ import annotations

import logging

from starlette.concurrency import run_in_threadpool

from app.table.upload import oss_client
from app.table.upload.import_service import drain_import_queue, reap_stale_uploads
from app.table.upload.import_source import ImportSource, OSSObjectSource
from app.table.upload.oss_client import OSSObjectNotFound
from config import settings

logger = logging.getLogger(__name__)


def oss_source_factory(object_key: str) -> ImportSource:
    """object key → 可流式读取的数据源。排队批次只有 OSS 的（本地退路重建不出源）。"""
    return OSSObjectSource(oss_client.object_stream, object_key)


def oss_object_size(object_key: str) -> int | None:
    """对象大小；对象不存在返回 ``None``。回收超时直传批次时用它分流。

    「不存在」与「存在但读不到」必须分开：前者是用户没传完（判失败），后者是基础设施
    出问题（不该据此把一份可能好好躺在 OSS 里的文件判死）。故只有确认 404 才返回 None，
    其余异常照常抛出，由调用方按「无法确认」处置。
    """
    try:
        return oss_client.stat_object(object_key)
    except OSSObjectNotFound:
        return None


def _reap_stale_uploads() -> None:
    """回收超时的直传批次。OSS 未启用时整条路径不存在，直接跳过。

    异常在这里就地吞掉：回收是轮询的附带工作，它失败不该连累排队接手——那是两件事，
    而把它们绑在一起意味着 OSS 抖一下就没人接排队批次了。
    """
    if not settings.OSS_ENABLED:
        return
    try:
        reap_stale_uploads(
            object_size=oss_object_size,
            max_file_size=settings.MAX_FILE_SIZE,
            stale_seconds=settings.upload_stale_seconds,
        )
    except Exception:
        logger.exception("回收超时直传批次时出错，本轮跳过")


async def reap_stale_uploads_round() -> bool:
    """轮询 job：回收超时的直传批次。是维护性任务，恒返回 ``False``——它每一轮都会
    被执行到，但不参与"整轮要不要重来"的判定，见 ``app/jobs/poller.py``。
    """
    await run_in_threadpool(_reap_stale_uploads)
    return False


async def drain_one_batch_round() -> bool:
    """轮询 job：接手一个排队批次。跑完立刻再来一轮（不等满一个间隔），好让积压的
    队列连着排空——因此接到批次时返回 ``True``。

    导入是同步阻塞的、可达数十分钟，必须交给线程池——直接 await 在事件循环上跑会把
    这个 worker 的全部请求一起卡住。这与上传接口把导入交给 BackgroundTasks 是同一
    条约束（占用同一个 anyio 线程限流器，见 config.THREAD_POOL_LIMIT 的说明）。
    """
    batch = await run_in_threadpool(drain_import_queue, source_factory=oss_source_factory)
    if batch is None:
        return False
    logger.info("排队批次已接手完成 %s: 状态=%s", batch.file_name, batch.status)
    return True
