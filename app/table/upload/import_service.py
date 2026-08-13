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
from datetime import UTC, date, datetime, timedelta

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
    STATUS_UPLOADING,
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
    """按 (site, period, report_date) 记录在榜与掉榜行数，供分页总数与日期选择器读取。

    两个计数与事实/掉榜写入在**同一事务**内提交。不一致的后果不是"数字略有偏差"——
    分页把这两个数当作两段行集合的分界线，边界错位会让某一行出现两次或整行消失。
    """
    session.execute(
        text(
            """
            INSERT INTO analysis.site_data_stats
                (site, period_type, report_date, row_count, dropped_count)
            VALUES (:site, :period, :rdate, :n, :d)
            ON CONFLICT (site, period_type, report_date)
            DO UPDATE SET row_count = EXCLUDED.row_count,
                          dropped_count = EXCLUDED.dropped_count,
                          updated_at = now()
            """
        ),
        {"site": result.site, "period": result.period, "rdate": result.report_date,
         "n": result.written_rows, "d": result.dropped_rows},
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
    batch_id: int | None = None,
    session_factory: sessionmaker = SessionFactory,
) -> ImportBatch:
    """执行一次导入。返回批次记录（含最终状态与计数）。

    ``source`` 是 :class:`ImportSource`（OSS 对象或本地文件）；``object_key`` 记入批次记录，
    使原始文件可溯源与重导。OSS-first 流程下不再做导入后本地归档——OSS 对象即原始归档。

    ``batch_id`` 是签发直传凭证时就落下的那条 ``UPLOADING`` 记录（见
    :func:`create_upload_batch`）。给了就**推进它**而不是新建，否则一次上传会在历史里
    留下两条记录——一条永远停在「上传中」，一条从排队开始，用户看不出它们是同一件事。
    本地退路没有签发这一步，不传即新建。

    不抛异常给调用方（后台任务），失败信息落在批次记录的 status/error_message 里。
    唯一的例外 :class:`ImportRejected` 表示未拿到串行锁，调用方可据此提示稍后重试。
    """
    if period not in VALID_PERIOD_TYPES:
        raise ValueError(f"period 只支持 {sorted(VALID_PERIOD_TYPES)}，收到 {period!r}")
    site = normalize_site_code(site)

    session = session_factory()
    try:
        # 批次记录先落库（PROCESSING 之前先建，保证每次尝试都可追溯）。
        batch = session.get(ImportBatch, batch_id) if batch_id is not None else None
        if batch is None:
            batch = ImportBatch(
                site=site, period_type=period, report_date=report_date,
                file_name=original_filename, object_key=object_key or "",
                status=STATUS_QUEUED,
            )
            session.add(batch)
        else:
            batch.status = STATUS_QUEUED
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


def create_upload_batch(
    *,
    site: str,
    period: str,
    report_date: date,
    original_filename: str,
    object_key: str,
    session_factory: sessionmaker = SessionFactory,
) -> ImportBatch:
    """在**签发直传凭证之前**落一条 ``UPLOADING`` 批次记录，返回它。

    这是本模块里唯一一个「导入还没开始就写记录」的函数，理由是直传那一段服务端本来
    什么都不知道：字节不经应用服务器，用户关掉标签页、断网、或分片上传最后一步失败，
    此前库里连一条记录都没有——用户以为传上去了，历史屏也查不到。

    object key 在签发那一刻就已确定（凭证策略正是限死到这个 key 上），所以记录从落下
    的第一刻起就是完整可溯源的，不存在「先占个位、回头补齐」的中间态。

    调用方 MUST 先调本函数、成功之后再签发凭证。反过来的顺序会精确重建这个函数要消灭的
    那个状态：一份能用的凭证配一条不存在的记录。
    """
    if period not in VALID_PERIOD_TYPES:
        raise ValueError(f"period 只支持 {sorted(VALID_PERIOD_TYPES)}，收到 {period!r}")
    site = normalize_site_code(site)

    session = session_factory()
    try:
        batch = ImportBatch(
            site=site, period_type=period, report_date=report_date,
            file_name=original_filename, object_key=object_key,
            status=STATUS_UPLOADING,
        )
        session.add(batch)
        session.commit()
        session.refresh(batch)
        logger.info("已登记直传批次 #%s: %s", batch.id, original_filename)
        return batch
    finally:
        session.close()


def claim_upload_batch(
    object_key: str,
    *,
    session_factory: sessionmaker = SessionFactory,
) -> int | None:
    """按 object key 认领处于 ``UPLOADING`` 的批次，返回其 id；认领不到返回 ``None``。

    object key 含 uuid、一次上传对应一个，不存在误认领。

    认领不到是正常情形而非错误：回收任务可能刚把它判成 ``FAILED``，也可能调用方跳过
    签发直接触发导入（测试与运维脚本会这么干）。调用方据此新建即可——导入触发接口的
    契约是「凭 key 触发导入」，它不该因为另一条链路的记账状态而变得不可用。
    """
    session = session_factory()
    try:
        return session.execute(
            select(ImportBatch.id).where(
                ImportBatch.object_key == object_key,
                ImportBatch.status == STATUS_UPLOADING,
            ).order_by(ImportBatch.created_at.desc()).limit(1)
        ).scalars().first()
    finally:
        session.close()


def reap_stale_uploads(
    *,
    object_size: Callable[[str], int | None],
    max_file_size: int,
    stale_seconds: int,
    session_factory: sessionmaker = SessionFactory,
) -> list[ImportBatch]:
    """收尾超时的 ``UPLOADING`` 批次。返回本轮处置过的批次。

    **没有收尾的状态就是个死标签。** 用户关掉标签页之后没有任何东西会回头看这条记录，
    界面永远显示「上传中」——相对于此前的「什么都不显示」，那只是换了句谎话。这与
    ``QUEUED`` 必须有接手者是同一条纪律。

    判据不是「多久算久」，而是**这次上传还有没有可能完成**：直传的每个请求都由 STS 临时
    凭证签名，凭证过期后后续分片一律被拒。``stale_seconds`` 因此由凭证有效期推导
    （``settings.upload_stale_seconds``），不是一个可与之矛盾的独立旋钮。

    超时之后按对象是否真的在 OSS 分流（``object_size`` 返回 ``None`` 表示对象不存在）：

    - **在** → 字节传完了，只是导入没被触发（关标签页的典型形态）。置 ``QUEUED`` 交给
      :func:`drain_import_queue`，**不让用户重传**——文件已经在 OSS 了，重传 1.5GB 毫无道理。
      这条路径复用既有的接手/串行锁/失败落状态，不引入第二条导入路径。
    - **不在** → 判 ``FAILED``，写明请重新上传。

    **大小上限在这条路径上要重新执行一遍。** ``/oss/import`` 会在触发导入前称量并对超限
    拒绝，而这里绕过了那个端点——不补这一道，「不调 /oss/import」就成了绕过上限的路子。

    ``object_size`` 由调用方注入，本层因此不依赖 oss_client（与 ``drain_import_queue``
    的 ``source_factory`` 同一个理由：编排层不需要知道对象存储怎么读）。
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=stale_seconds)
    session = session_factory()
    handled: list[ImportBatch] = []
    try:
        stale = session.execute(
            select(ImportBatch).where(
                ImportBatch.status == STATUS_UPLOADING,
                ImportBatch.created_at < cutoff,
            ).order_by(ImportBatch.created_at)
        ).scalars().all()

        for batch in stale:
            _reap_one(session, batch, object_size, max_file_size)
            handled.append(batch)

        if handled:
            session.commit()
        return handled
    finally:
        session.close()


def _reap_one(
    session: Session,
    batch: ImportBatch,
    object_size: Callable[[str], int | None],
    max_file_size: int,
) -> None:
    """单条超时批次的处置。不 commit——由 :func:`reap_stale_uploads` 统一提交。

    称量对象这一步会走网络，可能抛（凭据失效、网络不通）。**一条抛了不能让整轮停下**，
    否则一个坏对象会挡住它后面所有批次的收尾；判为失败并写明原因即可，下一轮不再看它。
    """
    if not batch.object_key:
        # 理论上不会有：UPLOADING 只由 create_upload_batch 产生，那里 object_key 必填。
        batch.status = STATUS_FAILED
        batch.error_message = "上传未完成且没有可溯源的对象，请重新上传"
        return

    try:
        size = object_size(batch.object_key)
    except Exception as exc:
        logger.exception("回收直传批次 #%s 时无法确认对象状态", batch.id)
        batch.status = STATUS_FAILED
        batch.error_message = f"无法确认上传结果，请重新上传：{exc}"[:2000]
        return

    if size is None:
        logger.info("直传批次 #%s 超时且对象不存在，判为失败: %s", batch.id, batch.file_name)
        batch.status = STATUS_FAILED
        batch.error_message = "上传未完成或已中断，请重新上传"
        return

    if size > max_file_size:
        logger.warning("直传批次 #%s 的对象超过大小上限，拒绝导入: %s", batch.id, batch.file_name)
        batch.status = STATUS_FAILED
        batch.error_message = (
            f"文件超过上限（{size / 1024 ** 3:.2f} GB > "
            f"{max_file_size / 1024 ** 3:.2f} GB），请拆分后再导入"
        )
        return

    logger.info("直传批次 #%s 对象已在 OSS 但未触发导入，转入排队: %s", batch.id, batch.file_name)
    batch.status = STATUS_QUEUED


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
