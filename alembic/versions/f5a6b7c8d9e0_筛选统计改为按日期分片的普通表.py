"""筛选统计改为按日期分片的普通表，导入后只重算当期切片

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-08-05

`category_stats` / `brand_stats` 此前是物化视图，按 (站点, 周期) 聚合、每次导入
`REFRESH ... CONCURRENTLY` 一遍。两个毛病同源——**统计的粒度比筛选粗一层**：

1. **数字对不上。** 视图定义里没有 `report_date`，`exploded` 的 UNION 按
   (site, period_type, keyword_id, cid) 去重，于是计数是"整个保留窗口内、前三类目里
   包含该类目的**去重关键词数**"；而筛选只作用于**单个日期切片**。实测（US 日报两天）：
   下拉显示 `Electronics` 21944，实际筛 01-17 得 17497、筛 01-16 得 16609。
   这正是原迁移注释里说要避免的"选项显示 1200、筛出来 3000 行"，才两天就已经偏离。
2. **刷新与数据总量同阶。** 视图无日期过滤，`EXPLAIN` 显示对每个分区 `Seq Scan`，
   六路 UNION 去重后聚合，两张事实表全扫。实测 79.8 万行时刷一次 5.1s，而它在**每次
   导入后都跑**。保留窗口满窗约 2 亿行是 250 倍。

改成按 (站点, 周期, 日期) 分片的普通表后，两件事一起解决：计数与筛选同粒度；导入后
只需 `DELETE` + 重算**当期这一个切片**，代价与切片大小同阶、不随窗口增长。

**存 id 不存名称。** 品牌基数 15.97 万/站/天，按日期分片后行数与日期数相乘，存名称等于
把同一个字符串重复几千万次。读路径是"取一个切片"，回 join 维度表走主键，代价可忽略。

**不留旧数据。** 旧视图是窗口级聚合，无法拆成按日期的分片（跨日期去重之后，每个日期
各自贡献了多少已经不可还原）。新表建成空的，由 `scripts/rebuild_filter_stats.py`
按已有切片回填，或等下一次导入自然写入。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from config import settings

revision: str = "f5a6b7c8d9e0"
down_revision: str | None = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA

# (表名, 维度 id 列名, id 类型, 注释里的东西)
_STATS_TABLES = (
    ("category_stats", "category_id", sa.SmallInteger(), "类目"),
    ("brand_stats", "brand_id", sa.Integer(), "品牌"),
)


def _create_stats_table(table: str, id_col: str, id_type, label: str) -> None:
    op.create_table(
        table,
        sa.Column("site", sa.String(8), nullable=False),
        sa.Column("period_type", sa.String(8), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column(id_col, id_type, nullable=False),
        sa.Column("cnt", sa.BigInteger(), nullable=False),
        # 主键前缀 (site, period_type, report_date) 正是读路径的作用域三元组，
        # 下拉取一个切片即主键范围扫描，不需要额外索引。
        sa.PrimaryKeyConstraint("site", "period_type", "report_date", id_col),
        schema=SCHEMA,
        comment=f"{label}下拉选项与计数，按 (站点,周期,日期,{label}) 分片。"
                f"口径与筛选一致：该切片内前三{label}里包含该项的关键词数。"
                f"导入后只重算当期切片（见 import_service._rebuild_filter_stats）。",
    )


def upgrade() -> None:
    # 旧的是物化视图，同名，必须先删
    op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {SCHEMA}.category_stats")
    op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {SCHEMA}.brand_stats")
    for table, id_col, id_type, label in _STATS_TABLES:
        _create_stats_table(table, id_col, id_type, label)


def downgrade() -> None:
    """退回窗口级聚合的物化视图（`WITH NO DATA`，需一次 REFRESH 才有内容）。

    恢复的是结构与旧口径，不是内容——按日期分片的数据无法反推成旧视图的跨日期去重计数，
    但那本来也是靠 REFRESH 从事实表重算的，不存在"丢数据"。
    """
    for table, _, _, _ in _STATS_TABLES:
        op.drop_table(table, schema=SCHEMA)

    for table, dim, id_col, label_col, fact_col in (
        ("category_stats", "dim_category", "category_id", "top_category", "category"),
        ("brand_stats", "dim_brand", "brand_id", "top_brand", "brand"),
    ):
        branches = " UNION ".join(
            f"SELECT site, '{period}'::varchar(8) AS period_type, keyword_id, "
            f"top{n}_{fact_col}_id AS did FROM {SCHEMA}.{fact} "
            f"WHERE top{n}_{fact_col}_id IS NOT NULL"
            for period, fact in (("DAY", "fact_daily"), ("WEEK", "fact_weekly"))
            for n in (1, 2, 3)
        )
        op.execute(
            f"""
            CREATE MATERIALIZED VIEW {SCHEMA}.{table} AS
            WITH exploded AS ({branches})
            SELECT e.site, e.period_type, d.name AS {label_col}, count(*) AS cnt
              FROM exploded e JOIN {SCHEMA}.{dim} d ON d.{id_col} = e.did
             GROUP BY e.site, e.period_type, d.name
            WITH NO DATA
            """
        )
        op.execute(
            f"CREATE UNIQUE INDEX uq_{table} ON {SCHEMA}.{table} "
            f"(site, period_type, {label_col})"
        )
