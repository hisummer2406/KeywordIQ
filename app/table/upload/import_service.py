"""V2 导入编排层。

把三段式核心 :func:`app.table.upload.importer.import_report` 包起来，负责：

- **串行化**：用 PostgreSQL 咨询锁保证同一时刻只有一个导入在跑（2C4G 上两个 GB 级
  导入并发会互相争抢 WAL 与索引构建）。拿不到锁的置为 QUEUED，不并行。
- **批次记录**：全程记录 site/period/report_date/行数/耗时/状态。
- **导入后统计回写**（在事实事务提交之后、独立进行）：更新 site_data_stats，并重算
  刚导入那个切片的筛选下拉统计（类目 / 品牌）。
- **原始文件归档**：成功后移到归档目录（可替换为 OSS）；失败则保留原文件供排查。

事务边界由本层控制，crud/importer 不 commit。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.common.errors import CODE_UPLOAD_IMPORT_REJECTED, ConflictError
from app.site_registry import normalize_site_code
from app.table.analysis.filter_stats import rebuild_filter_stats
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


def _rebuild_filter_stats(session: Session, result: ImportResult) -> None:
    """重算筛选下拉的统计——**只算刚导入的这一个切片**。

    实现在 `app.table.analysis.filter_stats`：演示/合成数据脚本要写同样的两张表，
    统计口径只能有一处定义。漏改其中一份的表现是"下拉里的数字对不上"。
    """
    rebuild_filter_stats(
        session, site=result.site, period=result.period, report_date=result.report_date
    )


@contextmanager
def _import_lock(session: Session) -> Iterator[bool]:
    """在**专属连接**上持有导入串行锁；``yield`` 出是否拿到，退出时释放。

    锁必须有自己的连接，不能借 Session 的：咨询锁是会话（连接）级的，而 Session 每次
    ``commit()`` 都会把连接还给池子——池里只要有别的活动（健康检查、导出、另一次查询），
    下一条语句就可能换到另一条连接上，之后的 ``pg_advisory_unlock`` 便跑在错的连接上，
    **静默返回 false、什么也没解开**。锁就此挂在池里那条空闲连接上直到进程退出，此后
    每一次导入都被判为「已有导入在进行」，而库里根本没有导入在跑。

    实测确认过：取锁 pid 56739 → commit → 池子被别的会话用过 → pid 56741，unlock 返回
    False。这个泄漏之所以能藏住，是因为同一条连接再抢自己已持有的锁会成功（可重入），
    于是后续导入照跑不误——直到某次拿到别的连接为止。

    附带的好处：进程被杀时连接随之关闭，PostgreSQL 自动释放会话级锁，不留孤儿锁。

    代价是导入期间多占**一条**同步连接（导入本就串行，故是全局 +1，不随并发增长）。
    """
    conn = session.get_bind().connect()
    got = False
    try:
        got = bool(
            conn.execute(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": _IMPORT_LOCK_KEY}
            ).scalar()
        )
        conn.commit()  # 不把连接留在 idle in transaction
        yield got
    finally:
        try:
            if got:
                conn.execute(
                    text("SELECT pg_advisory_unlock(:k)"), {"k": _IMPORT_LOCK_KEY}
                )
                conn.commit()
        finally:
            # 即便解锁语句失败，关连接也会让 PostgreSQL 释放它，锁不会留下。
            conn.close()


def _execute_locked(session: Session, batch: ImportBatch, source: ImportSource) -> ImportBatch:
    """在**已持有串行锁**的前提下跑完一个批次，并把终态落到批次记录上。

    锁的取与放都在调用方的 :func:`_import_lock` 里（它有自己的连接），本函数不碰锁。

    调用方是两条：首次提交（:func:`run_import`）与排队后被接手（:func:`drain_import_queue`）。
    两条必须走同一段代码——导入后的统计回写（``site_data_stats`` 与筛选统计）若只在其中
    一条上做，被 drain 接手的那一期数据会写进事实表却在界面上完全看不见。
    """
    started = time.monotonic()
    try:
        batch.status = STATUS_PROCESSING
        session.commit()

        # 事实导入（三段式）在一个事务里完成。
        result = import_report(
            session, site=batch.site, period=batch.period_type,
            report_date=batch.report_date, source=source,
        )
        session.commit()

        # 导入后处理：两类统计回写，在事实事务之外。都只涉及刚导入的那个切片。
        _update_site_data_stats(session, result)
        session.commit()
        _rebuild_filter_stats(session, result)
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
            batch.file_name, result.written_rows, result.skipped_rows, source.location,
        )
        return batch
    except Exception as exc:
        # 必须是 exception 不是 error：这条路径跑在后台任务里，全局异常处理器不拦
        # BackgroundTasks，栈是这条路径唯一的线索来源。
        logger.exception("导入失败 %s", batch.file_name)
        session.rollback()
        try:
            batch.status = STATUS_FAILED
            batch.error_message = str(exc)[:2000]
            batch.processing_seconds = int(time.monotonic() - started)
            session.commit()
        except Exception:
            logger.exception("写入失败状态时再次出错，批次记录可能停留在 PROCESSING")
            session.rollback()
        # 失败无碍原始文件留存：OSS 对象仍在，可据 object key 重导排查。
        return batch


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

        with _import_lock(session) as got_lock:
            if not got_lock:
                _mark_not_started(session, batch)
                raise ImportRejected("另一个导入正在进行，请稍后重试")
            return _execute_locked(session, batch, source)
    finally:
        session.close()


def _mark_not_started(session: Session, batch: ImportBatch) -> None:
    """没抢到串行锁时给批次一个**诚实**的状态。

    有 ``object_key`` 的批次留在 ``QUEUED``：源可从 OSS 重建，:func:`drain_import_queue`
    会在锁释放后接手，「排队中」名副其实。

    本地退路（``OSS_ENABLED=false``）没有 object_key，批次记录里只有文件名、没有任何
    能重建数据源的东西——留在 QUEUED 就是撒谎：界面显示「队列中」，而没有任何东西会
    回头看它。所以当场判失败并说清楚要重传，让用户看得见需要动手。
    """
    if batch.object_key:
        logger.warning("已有导入在进行，本次排队等待接手: %s", batch.file_name)
    else:
        logger.warning("已有导入在进行，本地退路无法自动重排，判为失败: %s", batch.file_name)
        batch.status = STATUS_FAILED
        batch.error_message = "已有导入正在进行；本地上传无法自动重排，请稍后重新上传"
    session.commit()


def drain_import_queue(
    *,
    source_factory: Callable[[str], ImportSource],
    session_factory: sessionmaker = SessionFactory,
) -> ImportBatch | None:
    """接手**一个**排队批次跑掉。没有排队批次、或锁被占着，都返回 ``None``。

    一次只接一个：串行约束不变，跑完这个之后下一轮轮询自然会接下一个。要是在这里循环
    把队列一次抽干，一个卡住的批次就会把整轮堵死，而调用方（轮询任务）看到的是「这次
    调用一直没返回」。

    ``source_factory`` 由调用方注入（object key → 数据源），本层因此不依赖 oss_client：
    编排层不需要知道对象存储怎么读。
    """
    session = session_factory()
    try:
        batch = session.execute(
            select(ImportBatch)
            .where(ImportBatch.status == STATUS_QUEUED, ImportBatch.object_key != "")
            .order_by(ImportBatch.created_at)
            .limit(1)
        ).scalars().first()
        if batch is None:
            return None

        # 先查队列再抢锁：反过来会在队列为空时白拿一次锁。
        with _import_lock(session) as got_lock:
            if not got_lock:
                return None

            logger.info("接手排队批次 %s（%s）", batch.file_name, batch.object_key)
            try:
                source = source_factory(batch.object_key)
            except Exception as exc:
                # 源重建不出来（对象被删、配置变更……）。锁由 _import_lock 负责还，
                # 这里只负责把批次判死，别让它继续假装在排队。
                logger.exception("排队批次的数据源重建失败 %s", batch.file_name)
                batch.status = STATUS_FAILED
                batch.error_message = f"排队后无法重建数据源，请重新上传：{exc}"[:2000]
                session.commit()
                return batch

            return _execute_locked(session, batch, source)
    finally:
        session.close()
