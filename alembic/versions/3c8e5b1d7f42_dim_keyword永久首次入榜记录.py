"""dim_keyword 增加永久首次入榜记录，事实表删除 is_new

Revision ID: 3c8e5b1d7f42
Revises: d4e5f6a7b8c9
Create Date: 2026-10-19

issue #137 V1，见 openspec/changes/first-seen-keyword-history。

`fact_*.is_new` 是「本期之前 7 天没出现过」的滚动窗口口径，导入时算定写进事实行：掉榜 8 天
再回来又算新词、日周各算各的、补更早数据后会变陈旧。首次入榜改由 `dim_keyword` 上的永久
记录表达（该表不分区、不参与保留清理、行只增不删），因此事实行的 `is_new` 与它的部分索引
一并删除。

**既有关键词一律 `UNKNOWN`、日期为空**——迁移日不是任何词的首次入榜日，把它写进去会让上线
当天整库变成「首次入榜」，而且没有任何报错。能证明的最早日期由
`scripts/backfill_first_seen.py` 另行补成 `EARLIEST_OBSERVED`，不在这里做（1500 万行的全表
回填不该放进迁移事务）。

`first_seen_basis` 的默认值是常量，PG11+ 加列不重写表；`idx_dim_keyword_first_seen` 建在
全为 NULL 的列上，很快，但会短暂锁写 `dim_keyword`（alembic 事务内不能 CONCURRENTLY），
应在导入空闲时执行。

`downgrade` 把 `is_new` 加回来但值全为默认 false——旧口径本来就要废弃，不尝试重算。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from config import settings

revision: str = "3c8e5b1d7f42"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA
_FACTS = (("fact_daily", "report_date"), ("fact_weekly", "report_date_week"))


def upgrade() -> None:
    op.add_column(
        "dim_keyword",
        sa.Column("first_seen_date", sa.Date, nullable=True,
                  comment="首次入榜的报告期次日期（取自文件名日期，非上传/迁移时间）；依据未知时为空"),
        schema=SCHEMA,
    )
    op.add_column(
        "dim_keyword",
        sa.Column("first_seen_period_type", sa.String(4), nullable=True,
                  comment="首次入榜的日期来自日报(DAY)还是周报(WEEK)；周报来源只有周精度"),
        schema=SCHEMA,
    )
    op.add_column(
        "dim_keyword",
        sa.Column("first_seen_basis", sa.String(20), nullable=False, server_default="UNKNOWN",
                  comment="首次入榜依据：CONFIRMED 已确认首次 / EARLIEST_OBSERVED 最早可观测"
                          "(此前可能见过) / UNKNOWN 无证据"),
        schema=SCHEMA,
    )
    op.create_index("idx_dim_keyword_first_seen", "dim_keyword", ["site", "first_seen_date"],
                    schema=SCHEMA)

    for table, _ in _FACTS:
        op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.idx_{table}_new")
        op.drop_column(table, "is_new", schema=SCHEMA)


def downgrade() -> None:
    for table, date_col in _FACTS:
        op.add_column(
            table,
            sa.Column("is_new", sa.Boolean, nullable=False, server_default=sa.false(),
                      comment="本站本周期内首次出现的关键词；有部分索引 idx_*_new 支撑新词筛选"),
            schema=SCHEMA,
        )
        op.create_index(
            f"idx_{table}_new", table, ["site", date_col, "current_ranking"],
            schema=SCHEMA, postgresql_where=sa.text("is_new"),
        )
    op.drop_index("idx_dim_keyword_first_seen", table_name="dim_keyword", schema=SCHEMA)
    for col in ("first_seen_basis", "first_seen_period_type", "first_seen_date"):
        op.drop_column("dim_keyword", col, schema=SCHEMA)
