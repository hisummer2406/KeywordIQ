"""分区表的分区管理。

分区缺失会让导入直接报错，是分区方案最常见的运维事故来源。因此"确保分区存在"
被刻意放在数据模型层交付，而不是留给导入链路——导入方只需在写入前调用一次，
不必了解分区的命名与边界规则。

事实表是两级::

    fact_daily                                     PARTITION BY RANGE (report_date)
    └── fact_daily_2026_07    [2026-07-01, 2026-08-01)   PARTITION BY LIST (site)
        ├── fact_daily_2026_07_us                  FOR VALUES IN ('US')
        ├── fact_daily_2026_07_gb                  FOR VALUES IN ('GB')
        └── fact_daily_2026_07_de                  FOR VALUES IN ('DE')

掉榜表只有一级::

    fact_dropped                                   PARTITION BY RANGE (anchor_date)
    └── fact_dropped_2026_07  [2026-07-01, 2026-08-01)   叶子

一级按月而非按天：按天会产生 365 × 站点数个叶子分区，规划时间开始变得可观，而
收益已被分区内 ``(site, report_date, ...)`` 的索引覆盖。按月时 12 个月 × 3 站
= 36 个叶子分区/表，PostgreSQL 在这个量级下规划开销可忽略。

事实表的二级按站点：业务要求站点数据物理隔离，同时把扫描再压到 1/站点数。掉榜表
不做这一层——它是差集而非全量，比事实表小一到两个数量级，裁剪收益索引已经够用。
每张表分不分站点由 :data:`PARTITIONED_TABLES` 单点定义，建分区与删分区都照它走。

本模块不提交事务，由调用方（service 层）控制事务边界。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.site_registry import normalize_site_code
from config import settings

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class PartitionScheme:
    """一张分区表的分区方案。

    ``site_subpartition`` 决定建的是两级还是一级分区。把它记在注册表里而不是散在调用点，
    是为了让「这张表分不分站点」只有一处答案——散开之后建分区与删分区两处各判一次，
    迟早会不一致。
    """

    date_column: str
    site_subpartition: bool


# 分区表 -> 其分区方案。新增分区表时在此登记，保留清理也照此表遍历。
PARTITIONED_TABLES: dict[str, PartitionScheme] = {
    "fact_daily": PartitionScheme("report_date", site_subpartition=True),
    "fact_weekly": PartitionScheme("report_date_week", site_subpartition=True),
    # 掉榜表只做一级月度 RANGE：它是差集而非全量，比事实表小一到两个数量级，
    # 二级分区的裁剪收益这个量级上索引已经够用，代价却是分区数翻倍。
    "fact_dropped": PartitionScheme("anchor_date", site_subpartition=False),
}


class UnknownFactTableError(ValueError):
    """表名不在 :data:`PARTITIONED_TABLES` 中。"""


def month_bounds(report_date: date) -> tuple[date, date]:
    """返回 ``report_date`` 所属自然月的 ``[起始, 下月起始)`` 边界。

    RANGE 分区的上界是开区间，所以返回的第二个值是下个月的 1 号。
    """
    start = report_date.replace(day=1)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def month_partition_name(table_name: str, report_date: date) -> str:
    """月度分区名，形如 ``fact_daily_2026_07``。"""
    start, _ = month_bounds(report_date)
    return f"{table_name}_{start:%Y_%m}"


def site_partition_name(table_name: str, report_date: date, site: str) -> str:
    """站点子分区名，形如 ``fact_daily_2026_07_us``。"""
    return f"{month_partition_name(table_name, report_date)}_{normalize_site_code(site).lower()}"


def _validate_table(table_name: str) -> PartitionScheme:
    """校验表名并返回其分区方案。

    表名只能来自 :data:`PARTITIONED_TABLES` 的键，因此后续可以安全地把它
    拼进 DDL——标识符无法参数化，白名单是这里唯一可靠的防线。
    """
    if table_name not in PARTITIONED_TABLES:
        known = ", ".join(sorted(PARTITIONED_TABLES))
        raise UnknownFactTableError(f"未知分区表 {table_name!r}，已登记的表为: {known}")
    return PARTITIONED_TABLES[table_name]


def ensure_fact_partitions(session: Session, table_name: str, site: str, report_date: date) -> None:
    """确保某事实表在给定站点与日期下的两级分区都存在。

    幂等：分区已存在时不报错、不重复创建，可以在每次导入前无条件调用。

    站点码会经过 :func:`~app.site_registry.normalize_site_code` 校验，未登记的站点
    直接抛错——绝不为一个拼错的站点码创建分区。

    **只能用同步会话（psycopg2）。** 下面的 ``CREATE TABLE ... PARTITION OF ...
    FOR VALUES FROM (:start) TO (:end)`` 带绑定参数，而 PostgreSQL 的扩展查询协议
    不允许在 DDL（utility statement）里用参数。psycopg2 在客户端做插值故可行；
    asyncpg 直接走协议，会报「the server expects 0 arguments for this query」。
    这与 ``COPY`` 一样，是导入路径必须保持同步的硬约束之一。
    """
    scheme = _validate_table(table_name)
    site_code = normalize_site_code(site)
    schema = settings.DATABASE_SCHEMA

    start, end = month_bounds(report_date)
    month_part = month_partition_name(table_name, report_date)

    # 一级：月度 RANGE 分区。RANGE 的分区键由父表定义决定，这里不必重复声明。
    # 两级方案下它自身再按站点做 LIST 分区；一级方案下它就是叶子。
    subpartition_clause = " PARTITION BY LIST (site)" if scheme.site_subpartition else ""
    session.execute(
        text(
            f'CREATE TABLE IF NOT EXISTS "{schema}"."{month_part}" '
            f'PARTITION OF "{schema}"."{table_name}" '
            f"FOR VALUES FROM (:start) TO (:end)"
            f"{subpartition_clause}"
        ),
        {"start": start, "end": end},
    )

    if not scheme.site_subpartition:
        logger.info(
            "已确保分区存在: %s.%s (区间=[%s, %s)，无站点二级分区)",
            schema, month_part, start, end,
        )
        return

    # 二级：站点 LIST 分区。
    site_part = site_partition_name(table_name, report_date, site_code)
    session.execute(
        text(
            f'CREATE TABLE IF NOT EXISTS "{schema}"."{site_part}" '
            f'PARTITION OF "{schema}"."{month_part}" '
            f"FOR VALUES IN (:site)"
        ),
        {"site": site_code},
    )

    logger.info(
        "已确保分区存在: %s.%s (站点=%s, 区间=[%s, %s))",
        schema, site_part, site_code, start, end,
    )


def ensure_partitions_for_import(session: Session, table_name: str, site: str, report_date: date) -> None:
    """导入前的入口：确保目标分区就绪。

    与 :func:`ensure_fact_partitions` 等价，单独命名是为了让导入链路的调用点意图明确。
    """
    ensure_fact_partitions(session, table_name, site, report_date)


def ensure_partitions_for_all_enabled_sites(
    session: Session, table_name: str, report_date: date
) -> None:
    """为配置中所有启用站点确保分区存在。

    用于预建下个月的分区（例如放在月末的定时任务里），避免每月第一次导入时才创建
    分区——那时创建会短暂持有父表的 ACCESS EXCLUSIVE 锁。
    """
    for site_code in settings.enabled_site_codes:
        ensure_fact_partitions(session, table_name, site_code, report_date)


def ensure_all_fact_partitions(session: Session, site: str, report_date: date) -> None:
    """为所有已登记的分区表同时确保分区存在（两张事实表 + 掉榜表）。"""
    for table_name in PARTITIONED_TABLES:
        ensure_fact_partitions(session, table_name, site, report_date)


# --------------------------------------------------------------------------
# 保留清理：移出超过热窗口的月度分区
#
# 月度 RANGE 分区是"只保留最近 N 个月"这条策略的地基：移出一个月 = DROP 该月的一级
# 分区，其站点子分区随父分区一并消失，是 O(1) 的元数据操作，不触发逐行删除、不产生
# 死元组、不影响窗口内其他月份。以下先是不碰库的纯函数（便于单测），再是读目录 + 执行
# DDL 的部分。
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MonthPartition:
    """一个月度一级分区及其 RANGE 边界 ``[start, end)``。"""

    name: str
    start: date
    end: date


def subtract_months(anchor: date, months: int) -> date:
    """返回 ``anchor`` 所在自然月往前推 ``months`` 个月的月初。

    ``months`` 可为 0（即本月月初）。跨年由零基月序的整除/取余自然处理。
    """
    zero_based = anchor.month - 1 - months
    year = anchor.year + zero_based // 12
    month = zero_based % 12 + 1
    return date(year, month, 1)


def retention_cutoff(today: date, retention_months: int) -> date:
    """保留下界（含）：报告日期早于该月初的数据视为超期。

    保留最近 ``retention_months`` 个自然月，即"本月 + 前 ``retention_months-1`` 个月"。
    例如 ``today=2026-07-15, retention_months=3`` → ``2026-05-01``，保留 5/6/7 月。

    一个月度分区 ``[start, end)`` 超期当且仅当 ``end <= cutoff``（等价于 ``start < cutoff``）。
    """
    if retention_months < 1:
        raise ValueError(f"retention_months 至少为 1，收到 {retention_months!r}")
    return subtract_months(today.replace(day=1), retention_months - 1)


def select_expired_partitions(
    partitions: list[MonthPartition], cutoff: date
) -> list[MonthPartition]:
    """从月度分区中挑出超期者（``end <= cutoff``），按 ``start`` 升序返回。"""
    expired = [p for p in partitions if p.end <= cutoff]
    return sorted(expired, key=lambda p: p.start)


# ``FOR VALUES FROM ('2026-07-01') TO ('2026-08-01')`` —— 解析月度 RANGE 分区的上下界。
_RANGE_BOUND_RE = re.compile(
    r"FROM \('(?P<start>\d{4}-\d{2}-\d{2})[^']*'\) TO \('(?P<end>\d{4}-\d{2}-\d{2})[^']*'\)"
)


def _parse_range_bounds(partbound: str) -> tuple[date, date] | None:
    """从 ``pg_get_expr(relpartbound)`` 文本解析出 ``[start, end)``。

    非 RANGE 分区（例如占位的 DEFAULT 分区）解析不出边界，返回 ``None``，由调用方跳过。
    """
    match = _RANGE_BOUND_RE.search(partbound)
    if match is None:
        return None
    return date.fromisoformat(match["start"]), date.fromisoformat(match["end"])


def list_month_partitions(session: Session, table_name: str) -> list[MonthPartition]:
    """枚举某事实表现有的一级月度分区及其边界。

    通过 PostgreSQL 目录（``pg_inherits`` + ``pg_class``）读取真实存在的分区，而不是
    按命名规则去猜——绝不因命名假设误删。
    """
    _validate_table(table_name)
    schema = settings.DATABASE_SCHEMA
    rows = session.execute(
        text(
            "SELECT c.relname, pg_get_expr(c.relpartbound, c.oid) AS partbound "
            "FROM pg_inherits i "
            "JOIN pg_class c ON c.oid = i.inhrelid "
            "JOIN pg_class p ON p.oid = i.inhparent "
            "JOIN pg_namespace n ON n.oid = p.relnamespace "
            "WHERE p.relname = :table AND n.nspname = :schema"
        ),
        {"table": table_name, "schema": schema},
    ).all()

    partitions: list[MonthPartition] = []
    for name, partbound in rows:
        bounds = _parse_range_bounds(partbound or "")
        if bounds is None:
            continue
        start, end = bounds
        partitions.append(MonthPartition(name=name, start=start, end=end))
    return partitions


def drop_month_partition(session: Session, table_name: str, partition_name: str) -> None:
    """移出单个月度分区：``DROP TABLE`` 会连带其站点子分区一并删除。

    本函数不提交事务，事务边界交由 service 层控制。``partition_name`` 必须来自
    :func:`list_month_partitions` 返回的真实分区名，不接受外部拼接的名字。
    """
    schema = settings.DATABASE_SCHEMA
    session.execute(text(f'DROP TABLE IF EXISTS "{schema}"."{partition_name}"'))
    logger.info("已移出月度分区: %s.%s", schema, partition_name)


def drop_partitions_older_than(
    session: Session, table_name: str, cutoff: date
) -> list[MonthPartition]:
    """移出某事实表中所有报告日期早于 ``cutoff`` 的月度分区。

    返回被移出的分区（按月份升序）。不提交事务。
    """
    expired = select_expired_partitions(list_month_partitions(session, table_name), cutoff)
    for part in expired:
        drop_month_partition(session, table_name, part.name)
    return expired
