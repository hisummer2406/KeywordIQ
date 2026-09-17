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

调度：清理是写操作、可能持有父表锁，不应放在 FastAPI 事件循环上。入口是可定时触发的
脚本 ``scripts/prune_retention.py``，由 cron / 容器定时任务在低峰调用。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.table.analysis import partitioning
from app.table.upload import import_service
from config import settings

logger = logging.getLogger(__name__)

# 按 (站点,周期,日期[,id]) 分片存量的预存统计表，日期选择器/无筛选总数/类目品牌下拉
# 唯一的数据来源。三张表都不在 PARTITIONED_TABLES 里（不是分区表，是普通表按切片增删），
# 分区移出时必须单独清理，否则残留行会让查询侧继续相信"这个日期还能查"（issue #100）。
_STALE_STATS_TABLES: tuple[str, ...] = ("site_data_stats", "category_stats", "brand_stats")


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

    @property
    def total_dropped(self) -> int:
        return sum(len(names) for names in self.dropped.values())

    @property
    def total_purged_stats(self) -> int:
        return sum(self.purged_stats.values())


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
                f'COPY "{schema}"."{partition_name}" TO STDOUT WITH (FORMAT csv, HEADER true)',
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
) -> RetentionResult:
    """对两张事实表按同一热窗口移出超期月度分区，并同步清理对应的预存统计残留。

    - ``today``：默认取当天，可注入以便测试或补跑。
    - ``retention_months``：默认取 ``settings.HOT_WINDOW_MONTHS``。
    - ``export_dir``：给定时，移出前把每个超期分区导出为 ``<分区名>.csv`` 留档。
    - ``dry_run``：只枚举将移出的分区与将清理的残留统计行数，不执行导出/DDL/DML、不提交。

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

    def _enumerate_and_mutate() -> None:
        if export_dir is not None and not dry_run:
            os.makedirs(export_dir, exist_ok=True)

        for table_name in partitioning.PARTITIONED_TABLES:
            expired = partitioning.select_expired_partitions(
                partitioning.list_month_partitions(session, table_name), cutoff
            )
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
