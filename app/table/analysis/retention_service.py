"""保留清理：只保留最近若干自然月的事实数据。

策略：``HOT_WINDOW_MONTHS`` 定义热窗口（默认 3 个月）。报告日期早于窗口下界的月度分区
被整体移出——日报（``fact_daily``）与周报（``fact_weekly``）统一适用同一窗口。移出前
可先把该月数据完整导出留档。

同一次清理还会同步清掉 ``site_data_stats``/``category_stats``/``brand_stats`` 中报告
日期早于保留下界的残留行（见 :func:`purge_stale_stats`）——这三张表是日期选择器、
无筛选总数、类目/品牌下拉唯一的数据来源，分区移出后不清理它们，查询侧会继续相信一个
物理上已经不存在的日期还能查（issue #100）。

分层约定：本模块是 service 层，控制事务边界（唯一的 ``commit`` 在这里）；真正的枚举与
DDL 由 :mod:`app.table.analysis.partitioning` 提供，那一层不提交。

归档闸门：分区 DROP 不可恢复且生产库没有备份，OSS 上的原始 CSV 是唯一的恢复来源。
某月每个站点、每个日报/周报期次的原始归档都校验通过，该月才允许移出（见
:func:`verify_month_archive`）；任一期次不通过或校验无法完成，该月全部分区表都保留。

调度：清理是写操作、可能持有父表锁，不应放在 FastAPI 事件循环上。入口是可定时触发的
脚本 ``scripts/prune_retention.py``，由运维机器人的定时任务在低峰调用。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.table.analysis import partitioning
from app.table.upload import import_service, oss_client
from app.table.upload.import_batch_model import STATUS_COMPLETED
from config import settings

logger = logging.getLogger(__name__)

# 按 (站点,周期,日期[,id]) 分片存量的预存统计表，日期选择器/无筛选总数/类目品牌下拉
# 唯一的数据来源。三张表都不在 PARTITIONED_TABLES 里（不是分区表，是普通表按切片增删），
# 分区移出时必须单独清理，否则残留行会让查询侧继续相信"这个日期还能查"（issue #100）。
_STALE_STATS_TABLES: tuple[str, ...] = ("site_data_stats", "category_stats", "brand_stats")

# 有独立原始文件的表及其批次周期。掉榜与增长榜是导入时由它们算出的派生数据，没有
# 自己的原始文件，随所在月份的判定结果一起走。
_ARCHIVE_SOURCE_TABLES: tuple[tuple[str, str], ...] = (("fact_daily", "DAY"), ("fact_weekly", "WEEK"))

REASON_NO_BATCH = "no_batch"
REASON_EMPTY_OBJECT_KEY = "empty_object_key"
REASON_NO_ROWS = "no_rows"
REASON_OBJECT_MISSING = "object_missing"
REASON_OBJECT_EMPTY = "object_empty"
REASON_OSS_UNAVAILABLE = "oss_unavailable"

ArchiveStat = Callable[[str], int]
"""给定 object key 返回对象字节数；对象不存在抛 ``OSSObjectNotFound``。"""


@dataclass(frozen=True)
class ArchiveGap:
    """一个未通过归档校验的期次。"""

    month: date
    site: str
    period_type: str
    report_date: date
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "month": self.month.strftime("%Y-%m"),
            "site": self.site,
            "period_type": self.period_type,
            "report_date": self.report_date.isoformat(),
            "reason": self.reason,
        }


@dataclass
class RetentionResult:
    """一次保留清理的结果。"""

    cutoff: date
    retention_months: int
    dry_run: bool
    # 表名 -> 被移出（或将被移出）的月度分区名，按月份升序。
    dropped: dict[str, list[str]] = field(default_factory=dict)
    # 表名 -> 导出留档的 CSV 路径（未开启导出时为空）。
    exported: dict[str, list[str]] = field(default_factory=dict)
    # 表名 -> 被清理（或将被清理）的预存统计残留行数。
    purged_stats: dict[str, int] = field(default_factory=dict)
    # True：本次运行没有拿到与导入共用的咨询锁，什么都没做（不移出分区、不清理统计、
    # 不提交），下次调度会重试。见 run_retention 文档。
    lock_skipped: bool = False
    # 未通过归档校验的期次；所在月份的全部分区本次都被保留。
    blocked: list[ArchiveGap] = field(default_factory=list)

    @property
    def total_dropped(self) -> int:
        return sum(len(names) for names in self.dropped.values())

    @property
    def total_purged_stats(self) -> int:
        return sum(self.purged_stats.values())

    @property
    def blocked_months(self) -> list[date]:
        return sorted({gap.month for gap in self.blocked})

    def to_dict(self) -> dict:
        """运维任务读取的结构化结果，字段即 ``--json`` 的对外约定。"""
        return {
            "status": "lock_skipped" if self.lock_skipped else "ok",
            "dry_run": self.dry_run,
            "cutoff": self.cutoff.isoformat(),
            "retention_months": self.retention_months,
            "dropped": self.dropped,
            "purged_stats": self.purged_stats,
            "blocked_months": [m.strftime("%Y-%m") for m in self.blocked_months],
            "blocked": [gap.to_dict() for gap in self.blocked],
        }


def _default_archive_stat(object_key: str) -> int:
    if not settings.OSS_ENABLED:
        raise RuntimeError("OSS 未启用，无法校验原始归档")
    return oss_client.stat_object(object_key)


def _latest_completed_batches(
    session: Session, start: date, end: date
) -> dict[tuple[str, str, date], tuple[str, int]]:
    """``[start, end)`` 内每个期次最新一次成功批次的 ``(object_key, written_rows)``。

    重导会先清后写，库内数据对应最后一次成功的批次，较早批次的文件不能代表现有数据。
    """
    rows = session.execute(
        text(
            f'SELECT DISTINCT ON (site, period_type, report_date) '
            f'       site, period_type, report_date, object_key, written_rows '
            f'FROM "{settings.DATABASE_SCHEMA}".import_batch '
            f"WHERE status = :status AND report_date >= :start AND report_date < :end "
            f"ORDER BY site, period_type, report_date, id DESC"
        ),
        {"status": STATUS_COMPLETED, "start": start, "end": end},
    ).all()
    return {(r[0], r[1], r[2]): (r[3], r[4]) for r in rows}


def _periods_with_data(
    session: Session, table_name: str, partition: partitioning.MonthPartition
) -> list[tuple[str, date]]:
    """枚举月度分区内有事实行的 ``(站点, 期次)``。

    逐日 ``EXISTS`` 点查而不是 ``SELECT DISTINCT``：后者要扫千万级行，前者命中
    ``(site, 日期, ...)`` 复合索引，每月每站点至多 31 次。
    """
    schema = settings.DATABASE_SCHEMA
    column = partitioning.PARTITIONED_TABLES[table_name].date_column
    stmt = text(
        f'SELECT EXISTS (SELECT 1 FROM "{schema}"."{partition.name}" '
        f'WHERE site = :site AND "{column}" = :day)'
    )
    periods: list[tuple[str, date]] = []
    for site in partitioning.list_site_subpartitions(session, partition.name):
        day = partition.start
        while day < partition.end:
            if session.execute(stmt, {"site": site, "day": day}).scalar():
                periods.append((site, day))
            day += timedelta(days=1)
    return periods


def verify_month_archive(
    session: Session,
    *,
    month: date,
    month_end: date,
    source_partitions: dict[str, partitioning.MonthPartition],
    archive_stat: ArchiveStat,
) -> list[ArchiveGap]:
    """校验一个超期月份的原始归档，返回未通过的期次；空列表表示可以移出。

    对象存储本身不可用时立刻停止：继续只会让每个期次各等一轮超时，而这一条已足以
    拦下整月。
    """
    batches = _latest_completed_batches(session, month, month_end)
    gaps: list[ArchiveGap] = []

    def gap(site: str, period: str, day: date, reason: str) -> ArchiveGap:
        return ArchiveGap(month=month, site=site, period_type=period, report_date=day, reason=reason)

    for table_name, period in _ARCHIVE_SOURCE_TABLES:
        partition = source_partitions.get(table_name)
        if partition is None:
            continue
        for site, day in _periods_with_data(session, table_name, partition):
            batch = batches.get((site, period, day))
            if batch is None:
                gaps.append(gap(site, period, day, REASON_NO_BATCH))
                continue
            object_key, written_rows = batch
            if not object_key:
                gaps.append(gap(site, period, day, REASON_EMPTY_OBJECT_KEY))
                continue
            if written_rows <= 0:
                gaps.append(gap(site, period, day, REASON_NO_ROWS))
                continue
            try:
                size = archive_stat(object_key)
            except oss_client.OSSObjectNotFound:
                gaps.append(gap(site, period, day, REASON_OBJECT_MISSING))
                continue
            except Exception:
                logger.exception("校验原始归档失败: %s", object_key)
                gaps.append(gap(site, period, day, REASON_OSS_UNAVAILABLE))
                return gaps
            if size <= 0:
                gaps.append(gap(site, period, day, REASON_OBJECT_EMPTY))
    return gaps


def export_partition_to_csv(session: Session, partition_name: str, dest_path: str) -> None:
    """用服务端 ``COPY`` 把一个分区导出为 CSV。

    ``partition_name`` 必须来自 :func:`partitioning.list_month_partitions` 返回的真实分区名。
    走 DBAPI 连接的 ``copy_expert`` 流式落盘，不把整表读入内存。
    """
    schema = settings.DATABASE_SCHEMA
    raw = session.connection().connection  # psycopg2 DBAPI 连接
    with open(dest_path, "w", encoding="utf-8", newline="") as fh:
        with raw.cursor() as cur:
            cur.copy_expert(
                # 必须是 COPY (SELECT ...)：月分区带站点子分区时本身是分区表，PG 拒绝直接 COPY。
                f'COPY (SELECT * FROM "{schema}"."{partition_name}") '
                "TO STDOUT WITH (FORMAT csv, HEADER true)",
                fh,
            )
    logger.info("已导出分区留档: %s -> %s", partition_name, dest_path)


def purge_stale_stats(session: Session, cutoff: date, *, dry_run: bool) -> dict[str, int]:
    """清理 ``_STALE_STATS_TABLES`` 中 ``report_date < cutoff`` 的残留行。

    **无条件执行，不依赖"这次是否真的 DROP 了分区"**：本变更上线前，保留清理从未清过
    这三张表，生产库可能已经积累了"分区早被移出、统计行却还在"的历史残留（见
    design.md 决策 2）。按 ``cutoff`` 独立判定才能在本变更上线后的第一次清理里把这些
    存量残留一并处理掉，不需要额外的一次性脚本。

    统计（``count(*)``）始终执行，供 dry-run 与日志使用；删除只在 ``dry_run=False``
    时执行。不提交事务——与 :func:`partitioning.drop_month_partition` 同一层约定，
    事务边界交由调用方（:func:`run_retention`）控制。

    三张表都没有 ORM 模型（``category_stats``/``brand_stats`` 是 ``filter_stats.py``
    直接管理的普通表），统一走 ``text()`` 原生 SQL，不为 ``site_data_stats`` 单独引入
    ORM ``delete()`` 语句——保持三张表清理逻辑同一种写法。

    **只按 ``report_date`` 过滤，不带 ``site``/``period_type`` 等值条件**，因此不能靠
    三张表主键 ``(site, period_type, report_date[, id])`` 的前缀走范围扫描——主键把
    `report_date` 排在第三位，缺等值条件时用不上它。`site_data_stats` 行数小（每
    (站点,周期,日期) 一行），全表扫可忽略；`category_stats`/`brand_stats` 按
    (站点,周期,日期,id) 分片，品牌基数约 15.97 万/站/天（见 `filter_stats.py`），三个
    月窗口叠起来是千万级行——没有专门索引的话，这一步的代价随**整张表**行数同阶，不是
    随残留月份数。迁移 ``47df76b2f424`` 给这两张表各补了一个 ``report_date`` 单列索引，
    专为这条清理路径存在；删掉那个索引会让这条注释里的复杂度分析重新变假，且不会有
    任何测试或报错提示这一点（PR #101 复审 P2）。
    """
    schema = settings.DATABASE_SCHEMA
    purged: dict[str, int] = {}
    for table in _STALE_STATS_TABLES:
        count = session.execute(
            text(f'SELECT count(*) FROM "{schema}"."{table}" WHERE report_date < :cutoff'),
            {"cutoff": cutoff},
        ).scalar_one()
        purged[table] = count
        if not dry_run and count:
            session.execute(
                text(f'DELETE FROM "{schema}"."{table}" WHERE report_date < :cutoff'),
                {"cutoff": cutoff},
            )
    return purged


def run_retention(
    session: Session,
    *,
    today: date | None = None,
    retention_months: int | None = None,
    export_dir: str | None = None,
    dry_run: bool = False,
    archive_stat: ArchiveStat | None = None,
) -> RetentionResult:
    """对两张事实表按同一热窗口移出超期月度分区，并同步清理对应的预存统计残留。

    - ``today``：默认取当天，可注入以便测试或补跑。
    - ``retention_months``：默认取 ``settings.HOT_WINDOW_MONTHS``。
    - ``export_dir``：给定时，移出前把每个超期分区导出为 ``<分区名>.csv`` 留档。
    - ``dry_run``：只枚举将移出的分区与将清理的残留统计行数，不执行导出/DDL/DML、不提交。
    - ``archive_stat``：称量归档对象的函数，默认走 OSS，仅供测试注入。归档校验没有开关，
      dry-run 与真实运行都会执行，被拦截的月份不会出现在 ``dropped`` 里。

    返回 :class:`RetentionResult`。同时服务于日常保留与首次上线的存量一次性清理——两者
    都是"移出所有超期分区"，只是存量场景一次会命中更多月份，务必先 ``dry_run`` 核对。

    统计清理（:func:`purge_stale_stats`）在每次调用都会执行，不管这次分区遍历有没有
    实际 DROP 什么——本变更上线前的历史残留同样靠这一步清掉，见 design.md 决策 2。

    **非 dry-run 时与导入共用同一把咨询锁**（:func:`app.table.upload.import_service.import_lock`）。
    没有这把锁，移出分区与一次正在进行的导入之间存在一个真实的交叠窗口：导入的事实写入
    与 ``site_data_stats`` 回写是两次**独立提交**（见 ``import_service._execute_locked``），
    如果保留清理恰好在这两次提交之间跑完"移出分区 + 清理统计 + 提交"，会先把导入刚写完
    事实行的分区 DROP 掉（此时清理不到任何统计残留，因为导入还没提交那一步），随后导入
    照常提交一条全新的 ``site_data_stats`` 行——事实已经不存在，统计却说有——正是本变更
    要消灭的 ``total>0, data=[]`` 状态，只是换了个触发路径（PR #101 复审 P1）。拿不到锁
    （另一个导入正在进行）就什么都不做、直接返回，``result.lock_skipped=True``，不重试、
    不阻塞——保留清理本来就是按调度周期性触发，下一轮自然会再试。dry-run 不需要这把锁：
    它只读、不提交，不存在与导入交叠后写坏数据的可能。
    """
    today = today or date.today()
    months = retention_months if retention_months is not None else settings.HOT_WINDOW_MONTHS
    cutoff = partitioning.retention_cutoff(today, months)

    result = RetentionResult(cutoff=cutoff, retention_months=months, dry_run=dry_run)
    stat = archive_stat or _default_archive_stat

    def _enumerate_and_mutate() -> None:
        if export_dir is not None and not dry_run:
            os.makedirs(export_dir, exist_ok=True)

        expired_by_table = {
            table_name: partitioning.select_expired_partitions(
                partitioning.list_month_partitions(session, table_name), cutoff
            )
            for table_name in partitioning.PARTITIONED_TABLES
        }

        # 全部月份校验完才开始第一条 DROP：判定结果不受删除进度影响，也让校验期间的
        # 网络往返不发生在持有分区排他锁的时候。
        months: dict[tuple[date, date], dict[str, partitioning.MonthPartition]] = {}
        for table_name, partitions in expired_by_table.items():
            for part in partitions:
                months.setdefault((part.start, part.end), {})[table_name] = part
        for (month, month_end), source_partitions in sorted(months.items()):
            result.blocked.extend(
                verify_month_archive(
                    session,
                    month=month,
                    month_end=month_end,
                    source_partitions=source_partitions,
                    archive_stat=stat,
                )
            )
        blocked_months = set(result.blocked_months)
        for month in sorted(blocked_months):
            logger.warning("保留清理拦截月份 %s：原始归档校验未通过，该月分区全部保留", month)

        for table_name, partitions in expired_by_table.items():
            expired = [p for p in partitions if p.start not in blocked_months]
            result.dropped[table_name] = [p.name for p in expired]

            if dry_run:
                continue

            if export_dir is not None:
                paths: list[str] = []
                for part in expired:
                    dest = os.path.join(export_dir, f"{part.name}.csv")
                    export_partition_to_csv(session, part.name, dest)
                    paths.append(dest)
                result.exported[table_name] = paths

            for part in expired:
                partitioning.drop_month_partition(session, table_name, part.name)

        result.purged_stats = purge_stale_stats(session, cutoff, dry_run=dry_run)

    if dry_run:
        _enumerate_and_mutate()
        # 只读枚举，不落任何变更。
        session.rollback()
    else:
        with import_service.import_lock(session) as got_lock:
            if not got_lock:
                logger.warning(
                    "保留清理跳过本次运行：未拿到与导入共用的咨询锁（另一个导入正在进行），"
                    "不移出任何分区、不清理任何统计，下次调度会重试。cutoff=%s", cutoff,
                )
                result.lock_skipped = True
                session.rollback()
                return result

            _enumerate_and_mutate()
            session.commit()

    logger.info(
        "保留清理完成: cutoff=%s, 保留月数=%s, dry_run=%s, 移出分区数=%s, 清理残留统计行数=%s",
        cutoff, months, dry_run, result.total_dropped, result.total_purged_stats,
    )
    return result
