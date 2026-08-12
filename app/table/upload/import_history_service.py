"""导入历史的业务层：查询参数规整、重导准入判定。

与 :mod:`app.table.upload.import_service` 的分工：那边是导入内核的编排（串行锁、批次
记录、统计回写），这边只回答两个问题——「这一页该返回什么」和「这个批次能不能重导」。
重导本身仍然走 ``run_import``，本模块不复制任何导入逻辑。

事务边界在本层（crud 层不 commit）。本模块的查询路径只读，不产生事务。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import (
    CODE_UPLOAD_BATCH_NOT_FOUND,
    CODE_UPLOAD_RETRY_NOT_ALLOWED,
    CODE_UPLOAD_SOURCE_NOT_RETAINED,
    BadRequestError,
    ConflictError,
    NotFoundError,
    UnprocessableError,
)
from app.site_registry import UnknownSiteError, normalize_site_code
from app.table.upload import import_batch_crud, oss_client
from app.table.upload.import_batch_crud import MAX_PER_PAGE
from app.table.upload.import_batch_model import (
    STATUS_FAILED,
    VALID_PERIOD_TYPES,
    VALID_STATUSES,
)
from app.table.upload.upload_schemas import ImportBatchRow

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetryPlan:
    """一次通过了全部准入判定的重导。

    只带重新执行导入所需的归属信息。**批次记录不在这里创建**——``run_import`` 自己会
    建一条新的（它在拿锁之前就先落库，保证每次尝试都可追溯）。这正是「重导新建记录、
    不改写原记录」得以成立的原因：重导走的是与首次导入完全相同的那条路径。
    """

    source_batch_id: int
    site: str
    period: str
    report_date: date
    file_name: str
    object_key: str


def _validate_site(site: str | None) -> str | None:
    """站点码规整。非法即报错，**不回退默认站点**——回退会让用户以为自己在看 GB 的历史，
    实际看的是 US 的。"""
    if not site:
        return None
    try:
        return normalize_site_code(site)
    except UnknownSiteError as exc:
        raise BadRequestError(f"未知的站点码：{site}", log_detail=str(exc)) from exc


def _validate_period(period: str | None) -> str | None:
    if not period:
        return None
    if period not in VALID_PERIOD_TYPES:
        raise BadRequestError(f"周期只支持 {'/'.join(sorted(VALID_PERIOD_TYPES))}，收到 {period}")
    return period


def _validate_status(status: str | None) -> str | None:
    if not status:
        return None
    if status not in VALID_STATUSES:
        raise BadRequestError(f"未知的状态：{status}")
    return status


def _validate_paging(page: int, per_page: int) -> tuple[int, int]:
    """分页参数。超上限**拒绝**而不是截断到上限。

    截断会产出一份与请求不符的分页：用户要第 2 页的 500 条，拿到的是第 2 页的 100 条，
    而界面用 total 与自己以为的 per_page 去算页数，得到的页码全是错的。
    """
    if page < 1:
        raise BadRequestError("页码必须从 1 开始")
    if per_page < 1:
        raise BadRequestError("每页条数必须大于 0")
    if per_page > MAX_PER_PAGE:
        raise BadRequestError(f"每页条数最多 {MAX_PER_PAGE} 条，收到 {per_page}")
    return page, per_page


async def query_batches(
    db: AsyncSession,
    *,
    site: str | None = None,
    period: str | None = None,
    status: str | None = None,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[ImportBatchRow], int]:
    """导入历史的一页。三项筛选均可缺省，缺省即不按该项筛。"""
    site = _validate_site(site)
    period = _validate_period(period)
    status = _validate_status(status)
    page, per_page = _validate_paging(page, per_page)

    rows, total = await import_batch_crud.list_batches(
        db, site=site, period=period, status=status, page=page, per_page=per_page
    )
    return [ImportBatchRow.from_batch(r) for r in rows], total


async def prepare_retry(db: AsyncSession, batch_id: int) -> RetryPlan:
    """重导的全部准入判定。通过则返回执行计划，不通过则抛错。

    **判定必须在触发后台任务之前全部做完。** 全局错误处理器不拦截 ``BackgroundTasks``：
    校验若放进后台任务，用户会看到一个「已触发」然后什么也不发生，失败只留在日志里。

    顺序是从便宜到昂贵：先查库（本地）、再查 OSS（一次网络往返），让能在本地否掉的
    请求不必付网络代价。
    """
    batch = await import_batch_crud.get_batch(db, batch_id)
    if batch is None:
        raise NotFoundError("导入批次不存在", code=CODE_UPLOAD_BATCH_NOT_FOUND)

    if batch.status != STATUS_FAILED:
        # 只允许 FAILED。QUEUED/PROCESSING 本就在执行中；COMPLETED 不允许，是因为重导会
        # 先删掉该站点/周期/期次的现有数据再重写——那份数据正在被查询使用，而重写周报
        # 要跑上好几分钟，期间该切片查不到东西。要重灌已完成的期次，走重新上传。
        raise ConflictError(
            f"只有导入失败的批次可以重导，当前状态为 {batch.status}",
            code=CODE_UPLOAD_RETRY_NOT_ALLOWED,
        )

    if not batch.object_key:
        # 本地文件退路（OSS_ENABLED=false）导入的批次没有留存原始文件，无从重建数据源。
        raise UnprocessableError(
            "该批次的原始文件未留存（本地导入），请重新上传该文件",
            code=CODE_UPLOAD_SOURCE_NOT_RETAINED,
        )

    # OSS 侧校验：越权前缀 + 对象仍在 + 未超大小上限。对象可能已被清理，不查就会产生一个
    # 注定失败的后台任务。oss2 是阻塞 SDK，走线程池，不占事件循环一整个网络往返。
    await run_in_threadpool(
        oss_client.verify_source_object,
        batch.object_key,
        batch.site,
        batch.period_type,
        batch.report_date,
    )

    return RetryPlan(
        source_batch_id=batch.id,
        site=batch.site,
        period=batch.period_type,
        report_date=batch.report_date,
        file_name=batch.file_name,
        object_key=batch.object_key,
    )
