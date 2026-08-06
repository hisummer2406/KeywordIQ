"""掉榜词表与掉榜计数

Revision ID: a7b8c9d0e1f2
Revises: f5a6b7c8d9e0
Create Date: 2026-08-06

分析屏的行集合从「锚定期这一个切片」放宽为「行窗口内出现过的关键词」，于是需要一处
存放**掉榜词**：在行窗口内出现过、但在锚定期没有事实数据的关键词。行窗口按周期取——
日报是锚定日往前 7 天，周报是锚定周往前 4 周。

**为什么建表而不在查询时算。** 掉榜集合 = 窗口内出现过 EXCEPT 当期出现。天视图要在
180 万行上做反连接、周视图 900 万行，而且**每翻一页重跑一次**。算定成一张表之后，成本
从「每次查询」降到「每期一次」，与事实表把排名变化、上期排名、是否新词都在写入时算定
是同一个道理。

**为什么只有一级分区。** 事实表是 `RANGE(日期) → LIST(site)` 两级；掉榜表只做月度
RANGE。它是差集而非全量，比事实表小一到两个数量级，二级分区的裁剪收益这个量级上索引
已经够用，代价却是分区数翻倍（站点数 × 月数）。站点因此是普通列，靠索引前缀裁剪。

**为什么必须分区。** 不分区它就成了一张只涨不掉的表——事实数据按月 DROP 滚出去了，
指向它们的掉榜记录还在。分区后保留清理照原样按月 DROP 即可
（见 `retention_service` 遍历 `partitioning.PARTITIONED_TABLES`）。

**手写而非 autogenerate**：分区表不在 SQLAlchemy 的 DDL 生成范围内，autogenerate 会把
它产成普通表并**静默丢掉** `PARTITION BY`。落库前用 `alembic upgrade head --sql` 核对
生成的 DDL 里确有 `PARTITION BY`。

叶子分区**不在这里创建**——与两张事实表一致，由 `partitioning.ensure_fact_partitions`
在导入前按需创建（幂等）。这里只建父表，避免迁移写死月份。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from config import settings

revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "f5a6b7c8d9e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA


def upgrade() -> None:
    op.create_table(
        "fact_dropped",
        sa.Column("site", sa.String(8), nullable=False, comment="ISO alpha-2 站点码"),
        sa.Column("period_type", sa.String(8), nullable=False, comment="DAY 或 WEEK"),
        sa.Column("anchor_date", sa.Date, nullable=False, comment="锚定期（分区键）"),
        sa.Column("keyword_id", sa.BigInteger, nullable=False, comment="→ dim_keyword"),
        sa.Column("last_seen_date", sa.Date, nullable=False, comment="窗口内最后一次在榜的期次"),
        sa.Column("last_ranking", sa.Integer, nullable=False, comment="最后一次在榜时的排名"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        # PostgreSQL 要求分区表的主键包含分区键；这里只有一级 RANGE(anchor_date)。
        sa.PrimaryKeyConstraint("site", "period_type", "anchor_date", "keyword_id"),
        schema=SCHEMA,
        postgresql_partition_by="RANGE (anchor_date)",
        comment="掉榜词：行窗口内出现过但锚定期无数据的关键词。导入时算定、查询时只读；"
                "按锚定期月度 RANGE 分区，随保留窗口一同移出；不做站点二级分区。",
    )

    # 掉榜段的分页：作用域三元组做前缀、最后在榜排名承担排序。索引顺序即分页顺序，
    # 因此计划里不该出现排序节点——出现了说明列序被改动过。
    op.create_index(
        "idx_fact_dropped_segment", "fact_dropped",
        ["site", "period_type", "anchor_date", "last_ranking"], schema=SCHEMA,
    )

    # 掉榜行数与在榜行数同粒度预存，分页总数才能保持 O(1)。site_data_stats 按
    # (站点,周期,期次) 一行、行数是三位数量级，带默认值的加列在这个体量下无碍
    # ——事实表上则绝不可以，那会触发整表重写。
    op.add_column(
        "site_data_stats",
        sa.Column("dropped_count", sa.BigInteger(), nullable=False, server_default="0",
                  comment="该期的掉榜词数（见 fact_dropped）"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    """回滚不丢原始事实——掉榜集合是可从事实表重算的派生数据。"""
    op.drop_column("site_data_stats", "dropped_count", schema=SCHEMA)
    # DROP TABLE 连带其所有月度分区。
    op.drop_table("fact_dropped", schema=SCHEMA)
