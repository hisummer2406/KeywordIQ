"""品牌与类目改为关键词级维度，商品维度收敛为 ASIN + 标题

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-08-04

**手写迁移**：涉及分区表与物化视图，autogenerate 对两者都不可靠——它看不见分区，
会把事实表当普通表处理，静默丢掉 PARTITION BY。

改动顺序有依赖，不能重排：

1. 建两张维度表（无依赖）
2. 事实表加 6 列（**不给 DEFAULT**，见下）
3. 重建 category_stats、新增 brand_stats（category_stats 依赖 dim_product.category，
   必须在第 4 步删列之前先 DROP 掉，否则删列会因视图依赖而失败）
3.5 建 6 个品牌筛选索引 —— **本迁移唯一与数据量成正比的一步**
4. 删 dim_product 的 brand/category 与两个 GIN 索引

**停机时长由第 3.5 步决定，不是由 SPA 构建决定。** 其余步骤都是 catalog 操作（秒级），
而建索引要真读一遍数据：满窗约 2 亿行 × 6 个索引，2C4G 上是小时级。空库首次上线不受
影响；已装满的库应当先按第 3.5 步注释里的 ONLY + CONCURRENTLY + ATTACH recipe 在线
把索引建好，之后本迁移的 IF NOT EXISTS 会空转而过。

**为什么 ADD COLUMN 不能带 DEFAULT**：PostgreSQL 11+ 对「可空、无默认值」的 ADD COLUMN
只改 catalog，是秒级的元数据操作；一旦带上 DEFAULT（哪怕是 0），旧版本会重写整张表。
本系统事实表满窗约 2 亿行、50 GB，重写意味着长时间持锁 + 磁盘翻倍。父表上的 ADD COLUMN
会自动传播到全部子分区，不需要逐分区执行。

**历史数据**：新列对既有分区全部为 NULL，且无法从现存数据推导——品牌#N 已被
DISTINCT ON 揉进 dim_product 且丢了名次与关键词的对应。唯一真值来源是 OSS 上的原始
对象，靠按批次重导补齐（见变更的第 7 组任务）。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from config import settings

revision: str = "d3e4f5a6b7c8"
down_revision: str | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA

# 六个新列：(列名, 类型)。品牌基数 13.5 万/站/天用 integer，类目 104 用 smallint
# ——类目占 3 列 × 2 亿行，用 integer 会白占一半空间。
_NEW_FACT_COLUMNS = [
    *((f"top{n}_brand_id", sa.Integer()) for n in (1, 2, 3)),
    *((f"top{n}_category_id", sa.SmallInteger()) for n in (1, 2, 3)),
]
_FACT_TABLES = ("fact_daily", "fact_weekly")


def _create_category_stats() -> None:
    """按**关键词级**类目重建：直接读事实表的三个类目槽位，不再 JOIN dim_product。

    计数口径随之改变——此前是「第一名商品的类目等于该值的行数」，之后是「前三类目里
    包含该值的关键词数」。必须与筛选口径一致，否则用户会看到「选项显示 1200、筛出来
    3000 行」这种对不上的数字。

    三个槽位用 UNION（不是 UNION ALL）去重：同一个关键词的前三类目若有重复值，
    该关键词对这个类目只应计一次。
    """
    op.execute(
        f"""
        CREATE MATERIALIZED VIEW {SCHEMA}.category_stats AS
        WITH exploded AS (
            SELECT site, 'DAY'::varchar(8) AS period_type, keyword_id, top1_category_id AS cid
              FROM {SCHEMA}.fact_daily WHERE top1_category_id IS NOT NULL
            UNION
            SELECT site, 'DAY'::varchar(8), keyword_id, top2_category_id
              FROM {SCHEMA}.fact_daily WHERE top2_category_id IS NOT NULL
            UNION
            SELECT site, 'DAY'::varchar(8), keyword_id, top3_category_id
              FROM {SCHEMA}.fact_daily WHERE top3_category_id IS NOT NULL
            UNION
            SELECT site, 'WEEK'::varchar(8), keyword_id, top1_category_id
              FROM {SCHEMA}.fact_weekly WHERE top1_category_id IS NOT NULL
            UNION
            SELECT site, 'WEEK'::varchar(8), keyword_id, top2_category_id
              FROM {SCHEMA}.fact_weekly WHERE top2_category_id IS NOT NULL
            UNION
            SELECT site, 'WEEK'::varchar(8), keyword_id, top3_category_id
              FROM {SCHEMA}.fact_weekly WHERE top3_category_id IS NOT NULL
        )
        SELECT e.site, e.period_type, c.name AS top_category, count(*) AS cnt
          FROM exploded e
          JOIN {SCHEMA}.dim_category c ON c.category_id = e.cid
         GROUP BY e.site, e.period_type, c.name
        WITH NO DATA
        """
    )
    op.execute(
        f"CREATE UNIQUE INDEX uq_category_stats ON {SCHEMA}.category_stats "
        f"(site, period_type, top_category)"
    )
    op.execute(
        f"COMMENT ON MATERIALIZED VIEW {SCHEMA}.category_stats IS "
        f"'类目下拉选项与计数，按 (站点,周期,类目) 聚合。口径是「前三类目里包含该类目的"
        f"关键词数」，与前三任一命中的筛选一致。每次导入后 REFRESH ... CONCURRENTLY。'"
    )


def _create_brand_stats() -> None:
    """品牌下拉选项，与 category_stats 同构。

    品牌基数远高于类目（13.5 万 vs 104），但下拉本就要计数排序取前若干，物化视图的
    行数与基数同阶、不与事实表行数同阶，仍然可控。
    """
    op.execute(
        f"""
        CREATE MATERIALIZED VIEW {SCHEMA}.brand_stats AS
        WITH exploded AS (
            SELECT site, 'DAY'::varchar(8) AS period_type, keyword_id, top1_brand_id AS bid
              FROM {SCHEMA}.fact_daily WHERE top1_brand_id IS NOT NULL
            UNION
            SELECT site, 'DAY'::varchar(8), keyword_id, top2_brand_id
              FROM {SCHEMA}.fact_daily WHERE top2_brand_id IS NOT NULL
            UNION
            SELECT site, 'DAY'::varchar(8), keyword_id, top3_brand_id
              FROM {SCHEMA}.fact_daily WHERE top3_brand_id IS NOT NULL
            UNION
            SELECT site, 'WEEK'::varchar(8), keyword_id, top1_brand_id
              FROM {SCHEMA}.fact_weekly WHERE top1_brand_id IS NOT NULL
            UNION
            SELECT site, 'WEEK'::varchar(8), keyword_id, top2_brand_id
              FROM {SCHEMA}.fact_weekly WHERE top2_brand_id IS NOT NULL
            UNION
            SELECT site, 'WEEK'::varchar(8), keyword_id, top3_brand_id
              FROM {SCHEMA}.fact_weekly WHERE top3_brand_id IS NOT NULL
        )
        SELECT e.site, e.period_type, b.name AS top_brand, count(*) AS cnt
          FROM exploded e
          JOIN {SCHEMA}.dim_brand b ON b.brand_id = e.bid
         GROUP BY e.site, e.period_type, b.name
        WITH NO DATA
        """
    )
    op.execute(
        f"CREATE UNIQUE INDEX uq_brand_stats ON {SCHEMA}.brand_stats "
        f"(site, period_type, top_brand)"
    )
    op.execute(
        f"COMMENT ON MATERIALIZED VIEW {SCHEMA}.brand_stats IS "
        f"'品牌下拉选项与计数，按 (站点,周期,品牌) 聚合。口径与筛选一致：前三品牌里"
        f"包含该品牌的关键词数。每次导入后 REFRESH ... CONCURRENTLY。'"
    )


def _create_legacy_category_stats() -> None:
    """downgrade 用：恢复旧的、建在 dim_product.category 上的定义。"""
    op.execute(
        f"""
        CREATE MATERIALIZED VIEW {SCHEMA}.category_stats AS
        SELECT f.site, 'DAY'::varchar(8) AS period_type, p.category AS top_category,
               count(*) AS cnt
          FROM {SCHEMA}.fact_daily f
          JOIN {SCHEMA}.dim_product p ON p.product_id = f.top1_product_id
         WHERE p.category <> ''
         GROUP BY f.site, p.category
        UNION ALL
        SELECT f.site, 'WEEK'::varchar(8), p.category, count(*)
          FROM {SCHEMA}.fact_weekly f
          JOIN {SCHEMA}.dim_product p ON p.product_id = f.top1_product_id
         WHERE p.category <> ''
         GROUP BY f.site, p.category
        WITH NO DATA
        """
    )
    op.execute(
        f"CREATE UNIQUE INDEX uq_category_stats ON {SCHEMA}.category_stats "
        f"(site, period_type, top_category)"
    )


def upgrade() -> None:
    # ---- 1. 两张维度表 ----
    op.create_table(
        "dim_brand",
        sa.Column("brand_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("name", name="uq_dim_brand_name"),
        schema=SCHEMA,
        comment="品牌维度（关键词级排行项，非商品属性）。",
    )
    op.create_table(
        "dim_category",
        sa.Column("category_id", sa.SmallInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("name", name="uq_dim_category_name"),
        schema=SCHEMA,
        comment="类目维度（关键词级排行项，非商品属性）。",
    )

    # ---- 2. 事实表加 6 列（父表执行，自动传播到子分区）----
    # 全部可空、无 server_default：这是"元数据操作"而非"重写整张表"的唯一前提。
    for table in _FACT_TABLES:
        for col_name, col_type in _NEW_FACT_COLUMNS:
            op.add_column(table, sa.Column(col_name, col_type, nullable=True), schema=SCHEMA)

    # ---- 3. 物化视图：先 DROP 旧的（它依赖 dim_product.category），再按新口径建 ----
    op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {SCHEMA}.category_stats")
    _create_category_stats()
    _create_brand_stats()

    # ---- 3.5 品牌筛选索引（方案 A：三个独立 btree）----
    # 「前三任一命中」是三列 OR，计划器用 BitmapOr 合并三路。方案在真实数据上实测选定
    # （US 周报 278.5 万行：无索引 499ms → 187ms；见 design 决策 4）。
    #
    # 选 btree 而非表达式 GIN 的决定性理由不是快那 67ms，而是**表达式 GIN 会把 SQL 形状
    # 焊死在索引上**：只有写成 ARRAY[...] && ARRAY[...] 才命中，_apply_filters 一旦改回
    # 自然的 IN ... OR ...，索引会静默失效退回全切片扫描，而测试不会报错。
    #
    # 列序 (site, 日期, 品牌)：前两列是作用域边界，每个查询都带；品牌在最后做等值。
    # 类目暂不建索引——基数只有 122，选择性太低，索引很可能不被选中（V1 有过投机建索引、
    # 实测 0 命中的先例），等有真实慢查询再说。
    #
    # ⚠️ **这是本迁移唯一与数据量成正比的一步，在已装满的库上它就是停机时长本身。**
    # 上面的 ADD COLUMN / DROP COLUMN 都是元数据操作（秒级），但建索引要真读一遍数据：
    # 保留窗口满窗约 2 亿行 × 6 个索引，2C4G 的 RDS 上是小时级，且分区父表的 CREATE INDEX
    # **不支持 CONCURRENTLY**，alembic 又把整个迁移包在一个事务里——期间事实表上的读写
    # 全部阻塞。空库（首次上线）不受影响，秒级完成。
    #
    # 用 IF NOT EXISTS 而不是 op.create_index，是为了让**先在线建好、再跑迁移**这条路走得通。
    # 表里已有数据时的正确做法是先离线把索引建出来，此处随后变成空转：
    #
    #     -- 1) 父表上建"空壳"索引，只改 catalog，不读数据
    #     CREATE INDEX idx_fact_daily_top1_brand ON ONLY analysis.fact_daily
    #            (site, report_date, top1_brand_id);
    #     -- 2) 逐个叶子分区并发建（三层结构，叶子是站点子分区 fact_daily_2026_07_us）
    #     CREATE INDEX CONCURRENTLY fact_daily_2026_07_us_top1_brand
    #         ON analysis.fact_daily_2026_07_us (site, report_date, top1_brand_id);
    #     -- 3) 挂回去；全部子分区挂齐后父索引自动转为 valid
    #     ALTER INDEX analysis.idx_fact_daily_top1_brand
    #         ATTACH PARTITION analysis.fact_daily_2026_07_us_top1_brand;
    #
    # 中间层（月分区 fact_daily_2026_07）同样要走 ONLY + ATTACH 这一层。
    for table, date_col in (("fact_daily", "report_date"), ("fact_weekly", "report_date_week")):
        for n in (1, 2, 3):
            op.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_top{n}_brand "
                f"ON {SCHEMA}.{table} (site, {date_col}, top{n}_brand_id)"
            )

    # ---- 4. 删 dim_product 的伪造属性 ----
    # 顺序在物化视图之后：category_stats 旧定义引用了 p.category，先删列会因依赖而失败。
    op.drop_index("idx_dim_product_brand_trgm", table_name="dim_product", schema=SCHEMA)
    op.drop_index("idx_dim_product_category_trgm", table_name="dim_product", schema=SCHEMA)
    op.drop_column("dim_product", "brand", schema=SCHEMA)
    op.drop_column("dim_product", "category", schema=SCHEMA)
    op.execute(
        f"COMMENT ON TABLE {SCHEMA}.dim_product IS "
        f"'商品维度（ASIN → 标题）。SCD Type 1：变化时原地覆盖，不留历史版本。'"
    )


def downgrade() -> None:
    """把 schema 恢复到旧代码可运行的形状。

    **数据回不来**——被删的两列本就是伪造属性（品牌#N 与商品#N 硬配的产物），
    重导也只能重新产生同样错误的值。这里恢复的是结构，不是内容：目的是让回滚后的
    旧代码不至于因为缺列而每个查询 500。
    """
    # 1. 恢复 dim_product 两列（空字符串填充，与旧模型的 default 一致）
    op.add_column("dim_product",
                  sa.Column("brand", sa.String(255), nullable=False, server_default=""),
                  schema=SCHEMA)
    op.add_column("dim_product",
                  sa.Column("category", sa.String(255), nullable=False, server_default=""),
                  schema=SCHEMA)
    op.create_index("idx_dim_product_brand_trgm", "dim_product", ["brand"],
                    unique=False, schema=SCHEMA, postgresql_using="gin",
                    postgresql_ops={"brand": "gin_trgm_ops"})
    op.create_index("idx_dim_product_category_trgm", "dim_product", ["category"],
                    unique=False, schema=SCHEMA, postgresql_using="gin",
                    postgresql_ops={"category": "gin_trgm_ops"})

    # 2. 物化视图回到旧定义
    op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {SCHEMA}.brand_stats")
    op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {SCHEMA}.category_stats")
    _create_legacy_category_stats()

    # 3. 事实表去掉 6 列（索引随列自动消失）
    for table in _FACT_TABLES:
        for col_name, _ in _NEW_FACT_COLUMNS:
            op.drop_column(table, col_name, schema=SCHEMA)

    # 4. 维度表
    op.drop_table("dim_category", schema=SCHEMA)
    op.drop_table("dim_brand", schema=SCHEMA)
