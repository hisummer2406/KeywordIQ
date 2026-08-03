"""V2 导入编排层。

把三段式核心 :func:`app.table.upload.importer.import_report` 包起来，负责：

- **串行化**：用 PostgreSQL 咨询锁保证同一时刻只有一个导入在跑（2C4G 上两个 GB 级
  导入并发会互相争抢 WAL 与索引构建）。拿不到锁的置为 QUEUED，不并行。
- **批次记录**：全程记录 site/period/report_date/行数/耗时/状态。
- **导入后刷新**（在事实事务提交之后、独立进行）：更新 site_data_stats、刷新类目物化视图。
- **原始文件归档**：成功后移到归档目录（可替换为 OSS）；失败则保留原文件供排查。

事务边界由本层控制，crud/importer 不 commit。
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.common.errors import CODE_UPLOAD_IMPORT_REJECTED, ConflictError
from app.site_registry import normalize_site_code
from app.table.upload.import_batch_model import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PROCESSING,
    STATUS_QUEUED,
    VALID_PERIOD_TYPES,
    ImportBatch,
)
from app.table.upload.import_source import ImportSource
from app.table.upload.importer import ImportResult, import_report
from database import SessionFactory

logger = logging.getLogger(__name__)

# 导入串行锁的固定 key（任意常量，全局一致即可）。'KI' 的十六进制。
_IMPORT_LOCK_KEY = 0x4B49


class ImportRejected(ConflictError):
    """导入被拒（另一个导入正在进行）→ 409。

    409 而非 503：这不是服务不可用，而是与当前状态冲突——同一时刻只允许一个导入
    （2C4G 上两个 GB 级导入并发会争抢 WAL 与索引构建）。
    """

    code = CODE_UPLOAD_IMPORT_REJECTED
    message = "已有导入正在进行，请稍后重试"


def _update_site_data_stats(session: Session, result: ImportResult) -> None:
    """按 (site, period, report_date) 记录行数，供分页总数与日期选择器读取。"""
    session.execute(
        text(
            """
            INSERT INTO analysis.site_data_stats (site, period_type, report_date, row_count)
            VALUES (:site, :period, :rdate, :n)
            ON CONFLICT (site, period_type, report_date)
            DO UPDATE SET row_count = EXCLUDED.row_count, updated_at = now()
            """
        ),
        {"site": result.site, "period": result.period,
         "rdate": result.report_date, "n": result.written_rows},
    )


def _refresh_category_stats(session: Session) -> None:
    """刷新类目物化视图。

    首次刷新（物化视图以 WITH NO DATA 创建）不能用 CONCURRENTLY——先判 relispopulated：
    未填充过则普通 REFRESH，之后才能 CONCURRENTLY（不阻塞读）。
    """
    populated = session.execute(
        text(
            "SELECT relispopulated FROM pg_class "
            "WHERE relname = 'category_stats' AND relnamespace = 'analysis'::regnamespace"
        )
    ).scalar()
    if populated:
        session.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY analysis.category_stats"))
    else:
        session.execute(text("REFRESH MATERIALIZED VIEW analysis.category_stats"))


def run_import(
    *,
    site: str,
    period: str,
    report_date: date,
    source: ImportSource,
    original_filename: str,
    object_key: str | None = None,
    session_factory: sessionmaker = SessionFactory,
) -> ImportBatch:
    """执行一次导入。返回批次记录（含最终状态与计数）。

    ``source`` 是 :class:`ImportSource`（OSS 对象或本地文件）；``object_key`` 记入批次记录，
    使原始文件可溯源与重导。OSS-first 流程下不再做导入后本地归档——OSS 对象即原始归档。

    不抛异常给调用方（后台任务），失败信息落在批次记录的 status/error_message 里。
    唯一的例外 :class:`ImportRejected` 表示未拿到串行锁，调用方可据此提示稍后重试。
    """
    if period not in VALID_PERIOD_TYPES:
        raise ValueError(f"period 只支持 {sorted(VALID_PERIOD_TYPES)}，收到 {period!r}")
    site = normalize_site_code(site)
    started = time.monotonic()

    session = session_factory()
    try:
        # 批次记录先落库（PROCESSING 之前先建，保证每次尝试都可追溯）。
        batch = ImportBatch(
            site=site, period_type=period, report_date=report_date,
            file_name=original_filename, object_key=object_key or "",
            status=STATUS_QUEUED,
        )
        session.add(batch)
        session.commit()

        # 串行锁：拿不到就保持 QUEUED 返回，不并行执行。
        got_lock = session.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": _IMPORT_LOCK_KEY}
        ).scalar()
        if not got_lock:
            logger.warning("已有导入在进行，本次置为排队: %s", original_filename)
            raise ImportRejected("另一个导入正在进行，请稍后重试")

        try:
            batch.status = STATUS_PROCESSING
            session.commit()

            # 事实导入（三段式）在一个事务里完成。
            result = import_report(
                session, site=site, period=period, report_date=report_date, source=source
            )
            session.commit()

            # 导入后处理：统计 + 物化视图刷新，在事实事务之外。
            _update_site_data_stats(session, result)
            session.commit()
            _refresh_category_stats(session)
            session.commit()

            # OSS-first：原始文件在入口即持久化于 OSS，OSS 对象即归档，不再做导入后本地归档。

            batch.status = STATUS_COMPLETED
            batch.total_rows = result.total_data_rows
            batch.written_rows = result.written_rows
            batch.processing_seconds = int(time.monotonic() - started)
            batch.completed_at = datetime.now()
            notes = []
            if result.skipped_rows:
                notes.append(f"跳过 {result.skipped_rows} 行（列数不符或搜索词为空）")
            if result.date_warning:
                notes.append(result.date_warning)
            if notes:
                batch.error_message = "；".join(notes)
            session.commit()

            logger.info(
                "导入完成 %s: 写入 %d 行, 跳过 %d 行, 源=%s",
                original_filename, result.written_rows, result.skipped_rows, source.location,
            )
            return batch
        finally:
            session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _IMPORT_LOCK_KEY})
            session.commit()

    except ImportRejected:
        raise
    except Exception as exc:
        logger.error("导入失败 %s: %s", original_filename, exc)
        session.rollback()
        try:
            batch.status = STATUS_FAILED
            batch.error_message = str(exc)[:2000]
            batch.processing_seconds = int(time.monotonic() - started)
            session.commit()
        except Exception:
            session.rollback()
        # 失败无碍原始文件留存：OSS 对象仍在，可据 object key 重导排查。
        return batch
    finally:
        session.close()
