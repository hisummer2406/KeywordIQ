"""保留清理：只保留最近若干自然月的事实数据。

策略：``HOT_WINDOW_MONTHS`` 定义热窗口（默认 3 个月）。报告日期早于窗口下界的月度分区
被整体移出——日报（``fact_daily``）与周报（``fact_weekly``）统一适用同一窗口。移出前
可先把该月数据完整导出留档。

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

from sqlalchemy.orm import Session

from app.table.analysis import partitioning
from config import settings

logger = logging.getLogger(__name__)


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

    @property
    def total_dropped(self) -> int:
        return sum(len(names) for names in self.dropped.values())


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


def run_retention(
    session: Session,
    *,
    today: date | None = None,
    retention_months: int | None = None,
    export_dir: str | None = None,
    dry_run: bool = False,
) -> RetentionResult:
    """对两张事实表按同一热窗口移出超期月度分区。

    - ``today``：默认取当天，可注入以便测试或补跑。
    - ``retention_months``：默认取 ``settings.HOT_WINDOW_MONTHS``。
    - ``export_dir``：给定时，移出前把每个超期分区导出为 ``<分区名>.csv`` 留档。
    - ``dry_run``：只枚举将移出的分区、不执行导出与 DDL、不提交。

    返回 :class:`RetentionResult`。同时服务于日常保留与首次上线的存量一次性清理——两者
    都是"移出所有超期分区"，只是存量场景一次会命中更多月份，务必先 ``dry_run`` 核对。
    """
    today = today or date.today()
    months = retention_months if retention_months is not None else settings.HOT_WINDOW_MONTHS
    cutoff = partitioning.retention_cutoff(today, months)

    result = RetentionResult(cutoff=cutoff, retention_months=months, dry_run=dry_run)

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

    if dry_run:
        # 只读枚举，不落任何变更。
        session.rollback()
    else:
        session.commit()

    logger.info(
        "保留清理完成: cutoff=%s, 保留月数=%s, dry_run=%s, 移出分区数=%s",
        cutoff, months, dry_run, result.total_dropped,
    )
    return result
