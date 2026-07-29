"""V2 数据模型：星型模型 + 月度站点分区

Revision ID: fa4c61e31f16
Revises:
Create Date: 2026-07-23

从零创建 V2 的全部结构。因为不迁移 V1 数据，这份迁移不需要描述旧结构，也不需要
``alembic stamp``——V2 用的是一台全新的实例。

**为什么手写而不用 autogenerate**：分区表（``PARTITION BY``）与物化视图都不在
SQLAlchemy 的 DDL 生成范围内，autogenerate 会把 ``fact_daily`` / ``fact_weekly``
产成普通表，静默地丢掉分区——那是最危险的一类"看起来成功了"。

各站点各月份的**叶子分区不在这里创建**，由 ``app.table.analysis.partitioning``
在导入前按需创建，避免这份迁移随时间腐化。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fa4c61e31f16'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "analysis"

# 份额字段统一精度，与源报表一致。
SHARE = sa.Numeric(10, 2)


def _fact_columns() -> list:
    """两张事实表共用的列定义。

    刻意不设 ``ranking_trend_day`` 之类的冗余派生字段——V1 的趋势 JSONB 之所以存在，
    是因为当时没有历史行；时序化之后 7 天走势由查询层从历史行计算。
    """
    return [
        sa.Column("site", sa.String(8), nullable=False, comment="ISO alpha-2 站点码"),
        sa.Column("keyword_id", sa.BigInteger, nullable=False, comment="→ dim_keyword"),
        sa.Column("current_ranking", sa.Integer, nullable=False, comment="当期搜索频率排名"),
        sa.Column("previous_ranking", sa.Integer, nullable=False, server_default="0", comment="上一期排名，写入时算定"),
        sa.Column("ranking_change", sa.Integer, nullable=False, server_default="0", comment="较上一期的变化，写入时算定"),
        sa.Column("is_new", sa.Boolean, nullable=False, server_default=sa.false(), comment="本站本周期内首次出现"),
        sa.Column("top1_product_id", sa.BigInteger, nullable=True, comment="→ dim_product"),
        sa.Column("top1_click_share", SHARE, nullable=False, server_default="0"),
        sa.Column("top1_conversion_share", SHARE, nullable=False, server_default="0"),
        sa.Column("top2_product_id", sa.BigInteger, nullable=True),
        sa.Column("top2_click_share", SHARE, nullable=False, server_default="0"),
        sa.Column("top2_conversion_share", SHARE, nullable=False, server_default="0"),
        sa.Column("top3_product_id", sa.BigInteger, nullable=True),
        sa.Column("top3_click_share", SHARE, nullable=False, server_default="0"),
        sa.Column("top3_conversion_share", SHARE, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def _create_fact_table(table_name: str, date_column: str, table_comment: str) -> None:
    """创建一张按 ``date_column`` 月度 RANGE 分区的事实表及其索引。

    PostgreSQL 要求分区表的主键必须包含分区键，主键 ``(site, <日期>, keyword_id)``
    同时覆盖了一级 RANGE 键与二级 LIST 键。
    """
    op.create_table(
        table_name,
        sa.Column(date_column, sa.Date, nullable=False, comment="报告日期（分区键）"),
        *_fact_columns(),
        sa.PrimaryKeyConstraint("site", date_column, "keyword_id"),
        schema=SCHEMA,
        postgresql_partition_by=f"RANGE ({date_column})",
        comment=table_comment,
    )

    # 7 天走势：按 (站点, 关键词) 定位后对日期做范围扫描。
    # 列序与主键相反是有意为之——主键的 (site, 日期, keyword_id) 服务不了这个查询，
    # 这是整套索引里最容易被漏掉的一个。
    op.create_index(
        f"idx_{table_name}_keyword_history", table_name,
        ["site", "keyword_id", date_column], schema=SCHEMA,
    )
    # 默认排序：当期排名。V1 实测最热路径（date_ranking_nonzero 8.2 万次扫描）。
    op.create_index(
        f"idx_{table_name}_ranking", table_name,
        ["site", date_column, "current_ranking"], schema=SCHEMA,
    )
    # 新词筛选 + 排名排序。部分索引，只覆盖 is_new 的少数行。
    # V1 该筛选被扫 5.9 万次，是真高频功能，用量与主列表同一量级。
    op.create_index(
        f"idx_{table_name}_new", table_name,
        ["site", date_column, "current_ranking"], schema=SCHEMA,
        postgresql_where=sa.text("is_new"),
    )
    # 按排名变化排序/筛选。
    op.create_index(
        f"idx_{table_name}_change", table_name,
        ["site", date_column, "ranking_change"], schema=SCHEMA,
    )
    # 类目/品牌筛选：先在 dim_product 上解析出 product_id，再回查事实表。
    op.create_index(
        f"idx_{table_name}_top1_product", table_name,
        ["site", date_column, "top1_product_id"], schema=SCHEMA,
    )
    # 份额排序。
    op.create_index(
        f"idx_{table_name}_click_share", table_name,
        ["site", date_column, "top1_click_share"], schema=SCHEMA,
    )
    op.create_index(
        f"idx_{table_name}_conversion_share", table_name,
        ["site", date_column, "top1_conversion_share"], schema=SCHEMA,
    )


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    # 子串搜索（ILIKE '%x%'）用 pg_trgm 三元组索引。界面的 keyword/title/brand/
    # category/asin 搜索都是子串匹配，全文 tsvector 索引对此无效。
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ---------- 维度表 ----------

    op.create_table(
        "dim_keyword",
        sa.Column("keyword_id", sa.BigInteger, autoincrement=True, nullable=False),
        sa.Column("site", sa.String(8), nullable=False, comment="ISO alpha-2 站点码"),
        sa.Column("keyword", sa.String(500), nullable=False, comment="搜索关键词原文"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("keyword_id"),
        sa.UniqueConstraint("site", "keyword", name="uq_dim_keyword_site_keyword"),
        schema=SCHEMA,
        comment="关键词维度：按站点隔离，(site,keyword) 唯一。子串搜索的 pg_trgm 索引建在此表"
                "（约 1500 万行）而非事实表（约 8 亿行），是星型模型最大的一处节省。",
    )
    # 关键词子串搜索。trigram GIN 对 ILIKE '%x%' 有效且大小写不敏感，建在这张
    # 约 500 万行/站的维度表上，而不是 8 亿行的事实表——后者会是几十 GB 且拖慢热写。
    op.execute(
        f"CREATE INDEX idx_dim_keyword_trgm ON {SCHEMA}.dim_keyword "
        f"USING gin (keyword gin_trgm_ops)"
    )

    op.create_table(
        "dim_product",
        sa.Column("product_id", sa.BigInteger, autoincrement=True, nullable=False),
        sa.Column("asin", sa.String(16), nullable=False, comment="亚马逊商品编号，10 位"),
        sa.Column("title", sa.String(500), nullable=False, server_default=""),
        sa.Column("brand", sa.String(255), nullable=False, server_default=""),
        sa.Column("category", sa.String(255), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("product_id"),
        sa.UniqueConstraint("asin", name="uq_dim_product_asin"),
        schema=SCHEMA,
        comment="商品维度（ASIN → 标题/品牌/类目）。SCD Type 1：变化时原地覆盖，不留历史版本，"
                "故历史事实行会关联到最新值；归档给 AI 训练时须把维度值物化进导出文件。",
    )
    # 四个字段都做子串 ILIKE 搜索，全部 trigram GIN，全部落在维度表。
    op.execute(f"CREATE INDEX idx_dim_product_title_trgm ON {SCHEMA}.dim_product USING gin (title gin_trgm_ops)")
    op.execute(f"CREATE INDEX idx_dim_product_brand_trgm ON {SCHEMA}.dim_product USING gin (brand gin_trgm_ops)")
    op.execute(f"CREATE INDEX idx_dim_product_category_trgm ON {SCHEMA}.dim_product USING gin (category gin_trgm_ops)")
    op.execute(f"CREATE INDEX idx_dim_product_asin_trgm ON {SCHEMA}.dim_product USING gin (asin gin_trgm_ops)")

    op.create_table(
        "sites",
        sa.Column("code", sa.String(8), nullable=False, comment="ISO alpha-2 站点码"),
        sa.Column("name_zh", sa.String(64), nullable=False, server_default=""),
        sa.Column("timezone", sa.String(64), nullable=False, server_default=""),
        sa.Column("domain", sa.String(128), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("code"),
        schema=SCHEMA,
        comment="站点参考表：仅供界面展示中文名/时区/域名。站点码合法性由应用层 site_registry 保证，"
                "此表不与事实表建外键。",
    )

    # ---------- 事实表（分区） ----------

    _create_fact_table(
        "fact_daily", "report_date",
        "日报事实表（约 30 万行/站/天）。按 report_date 月度 RANGE 分区 + 按 site 二级 LIST 分区。"
        "写入后不可变，纯追加；派生字段在写入时算定。",
    )
    _create_fact_table(
        "fact_weekly", "report_date_week",
        "周报事实表（约 300 万行/站/周）。结构与 fact_daily 一致，日期粒度为周。"
        "与日报是独立数据域，各自拥有一套 Top 商品字段。",
    )

    # ---------- 统计与批次 ----------

    op.create_table(
        "site_data_stats",
        sa.Column("site", sa.String(8), nullable=False),
        sa.Column("period_type", sa.String(8), nullable=False, comment="DAY 或 WEEK"),
        sa.Column("report_date", sa.Date, nullable=False),
        sa.Column("row_count", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("site", "period_type", "report_date"),
        schema=SCHEMA,
        comment="按 (站点,周期,期次) 的行数统计。同时服务两个用途：分页总数 O(1) 读取，"
                "以及日期选择器的可选日期列表。由导入链路在每次导入结束时写入。",
    )
    op.create_index(
        "idx_site_data_stats_lookup", "site_data_stats",
        ["site", "period_type", "report_date"], schema=SCHEMA,
    )

    op.create_table(
        "import_batch",
        sa.Column("id", sa.BigInteger, autoincrement=True, nullable=False),
        sa.Column("site", sa.String(8), nullable=False, comment="ISO alpha-2 站点码"),
        sa.Column("period_type", sa.String(8), nullable=False, comment="DAY 或 WEEK"),
        sa.Column("report_date", sa.Date, nullable=False, comment="报表自身的期次日期"),
        sa.Column("file_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="QUEUED"),
        sa.Column("total_rows", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("written_rows", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("processing_seconds", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
        comment="导入批次记录：每次导入可追溯到站点/周期/期次。status 含 QUEUED（导入串行，"
                "拿不到咨询锁时排队）。",
    )
    op.create_index(
        "idx_import_batch_site_period", "import_batch",
        ["site", "period_type", "report_date"], schema=SCHEMA,
    )
    op.create_index("idx_import_batch_status", "import_batch", ["status"], schema=SCHEMA)
    op.create_index("idx_import_batch_created", "import_batch", ["created_at"], schema=SCHEMA)

    # ---------- 用户表 ----------

    op.create_table(
        "user_center",
        sa.Column("id", sa.Integer, autoincrement=True, nullable=False),
        sa.Column("user_name", sa.String(255), nullable=False),
        sa.Column("hashed_pwd", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("is_super", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_name", name="uq_user_center_user_name"),
        schema=SCHEMA,
        comment="用户表：登录账号与口令散列。",
    )

    # ---------- 类目物化视图 ----------
    #
    # 两个视图的类目分布不同（日视图约 30 万行、周视图约 300 万行），不能共用一份。
    # 用物化视图而非普通视图：普通视图每次打开筛选面板都要聚合一次，200 并发下会
    # 打爆 2C4G。类目数据本就只在导入后变化，物化完全匹配这个更新频率。
    #
    # 唯一索引是 REFRESH ... CONCURRENTLY 的前提条件，不是可选项。
    op.execute(
        f"""
        CREATE MATERIALIZED VIEW {SCHEMA}.category_stats AS
        SELECT f.site,
               'DAY'::varchar(8) AS period_type,
               p.category        AS top_category,
               count(*)          AS cnt
          FROM {SCHEMA}.fact_daily f
          JOIN {SCHEMA}.dim_product p ON p.product_id = f.top1_product_id
         WHERE p.category <> ''
         GROUP BY f.site, p.category
        UNION ALL
        SELECT f.site,
               'WEEK'::varchar(8) AS period_type,
               p.category         AS top_category,
               count(*)           AS cnt
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
    op.execute(
        f"COMMENT ON MATERIALIZED VIEW {SCHEMA}.category_stats IS "
        f"'类目下拉选项与计数，按 (站点,周期,类目) 聚合。物化视图：数据只在导入后变化，"
        f"每次导入后 REFRESH ... CONCURRENTLY（依赖上面的唯一索引）。'"
    )

    # ---------- 补充关键字段注释（非显而易见的列）----------
    # 用一个集中的 COMMENT 块，避免在每个 create_table 里散落。obvious 的列（id/时间戳）不注释。
    _c = lambda tbl, col, txt: op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{tbl}.{col} IS '{txt}'"
    )
    # 站点码语义（多张表共用）
    for _t in ("dim_keyword", "fact_daily", "fact_weekly", "site_data_stats", "import_batch", "sites"):
        _col = "code" if _t == "sites" else "site"
        _c(_t, _col, "ISO 3166-1 alpha-2 大写站点码（英国用 GB 不用 UK），取值由应用层站点清单约束")
    _c("dim_product", "asin", "亚马逊商品编号，10 位")
    # 事实表派生字段（两表同构）
    for _t, _dcol in (("fact_daily", "report_date"), ("fact_weekly", "report_date_week")):
        _c(_t, _dcol, "报告日期（一级 RANGE 分区键）")
        _c(_t, "is_new", "本站本周期内首次出现的关键词；有部分索引 idx_*_new 支撑新词筛选")
        _c(_t, "top1_product_id", "点击量第 1 名商品 → dim_product.product_id（可空：无商品时）")
        _c(_t, "top2_product_id", "点击量第 2 名商品 → dim_product.product_id")
        _c(_t, "top3_product_id", "点击量第 3 名商品 → dim_product.product_id")
        _c(_t, "top1_click_share", "第 1 名商品的点击份额（%）")
        _c(_t, "top1_conversion_share", "第 1 名商品的转化份额（%）")
    _c("site_data_stats", "period_type", "周期类型：DAY 或 WEEK")
    _c("site_data_stats", "row_count", "该 (站点,周期,期次) 的事实行数，供分页总数直接读取")
    _c("import_batch", "period_type", "周期类型：DAY 或 WEEK")
    _c("import_batch", "status", "QUEUED（排队）/ PROCESSING / COMPLETED / FAILED")
    _c("import_batch", "total_rows", "源文件行数")
    _c("import_batch", "written_rows", "实际写入事实表的行数")


def downgrade() -> None:
    op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {SCHEMA}.category_stats")

    op.drop_table("user_center", schema=SCHEMA)
    op.drop_table("import_batch", schema=SCHEMA)
    op.drop_table("site_data_stats", schema=SCHEMA)

    # 事实表的叶子分区随父表一起 DROP，不必逐个处理。
    op.drop_table("fact_weekly", schema=SCHEMA)
    op.drop_table("fact_daily", schema=SCHEMA)

    op.drop_table("sites", schema=SCHEMA)
    op.drop_table("dim_product", schema=SCHEMA)
    op.drop_table("dim_keyword", schema=SCHEMA)
