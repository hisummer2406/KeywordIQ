"""持续增长词表与工具箱权限点

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-08-15

工具箱下的「持续增长市场」需要一处存放**每期算定的榜单**：近 7 期名次持续前进、且
幅度达标的关键词。口径固定（累计净增 > 1 万名，且 6 个日环比都 ≥10%），落在
`fact_dropped` 已经验证过的「导入时算定、查询时只读」模式里。

**为什么建表而不在查询时算。** 实时算是一次 210 万行（7 期 × 30 万）上的窗口聚合，
而且**每翻一页重跑一次**。算定成表之后，成本从「每次查询」降到「每期一次」。

**为什么只存命中行。** 存下所有「连续 7 期在榜」的词及其指标能让改阈值免回填，但那是
每站每期约 20 万行、三个月约 5400 万行——为一个以季度计频率的阈值调整长期背一张与事实
表同量级的表。代价是改阈值后旧期次仍是旧口径，须跑 `scripts/backfill_growth.py` 重算。

**为什么没有 period_type 列。** 只有日报有「连续 7 天」这个概念，周报没有。留一列
`period_type` 等于宣称 WEEK 也会出现在这张表里。

**为什么必须分区。** 不分区它就成了一张只涨不掉的表——事实数据按月 DROP 滚出去了，
指向它们的榜单记录还在。分区后保留清理照原样按月 DROP
（见 `retention_service` 遍历 `partitioning.PARTITIONED_TABLES`）。与掉榜表一致只做
一级 RANGE：每期数千行，比事实表小两到三个数量级，站点靠索引前缀裁剪即可。

**手写而非 autogenerate**：分区表不在 SQLAlchemy 的 DDL 生成范围内，autogenerate 会把
它产成普通表并**静默丢掉** `PARTITION BY`。落库前用 `alembic upgrade head --sql` 核对
生成的 DDL 里确有 `PARTITION BY`。

叶子分区**不在这里创建**——由 `partitioning.ensure_fact_partitions` 在导入/回填前按需
创建（幂等）。这里只建父表，避免把月份写死在迁移时刻。

同批种入权限点 `toolbox:view`（工具箱下全部屏幕共用一个，见 `app/auth/permissions.py`
里为什么不给专利/商标各开一个），授予 admin / operator / analyst 三个内置角色。迁移不
import 应用代码，按同一份矩阵硬编码。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from config import settings

revision: str = "f2a3b4c5d6e7"
down_revision: str | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA

# 与 app/auth/permissions.py 的矩阵保持一致
_PERMISSION = ("toolbox:view", "查看工具箱")
_GRANTED_TO = ("admin", "operator", "analyst")


def upgrade() -> None:
    op.create_table(
        "fact_growth",
        sa.Column("site", sa.String(8), nullable=False,
                  comment="ISO 3166-1 alpha-2 大写站点码（英国用 GB 不用 UK），"
                          "取值由应用层站点清单约束"),
        sa.Column("anchor_date", sa.Date, nullable=False, comment="锚定日（分区键），窗口右端"),
        sa.Column("keyword_id", sa.BigInteger, nullable=False, comment="→ dim_keyword"),
        sa.Column("start_ranking", sa.Integer, nullable=False, comment="窗口首期（d-6）排名"),
        sa.Column("end_ranking", sa.Integer, nullable=False, comment="锚定期（d）排名"),
        sa.Column("rank_gain", sa.Integer, nullable=False,
                  comment="累计净增名次 = start_ranking - end_ranking，正数为名次前进"),
        sa.Column("min_growth_rate", sa.Numeric(6, 4), nullable=False,
                  comment="窗口内 6 个日环比的最小值，如 0.1234 表示 12.34%"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        # PostgreSQL 要求分区表的主键包含分区键；这里只有一级 RANGE(anchor_date)。
        sa.PrimaryKeyConstraint("site", "anchor_date", "keyword_id"),
        schema=SCHEMA,
        postgresql_partition_by="RANGE (anchor_date)",
        comment="持续增长词：近 7 期名次持续前进且幅度达标的关键词。仅日报；导入时算定、"
                "查询时只读；按锚定日月度 RANGE 分区，随保留窗口一同移出；不做站点二级分区。",
    )

    # 榜单分页：作用域二元组做前缀、净增名次承担排序。不写 DESC——btree 可反向扫描，
    # 默认排序（净增降序）照样免排序节点，而带方向的索引会在 autogenerate 里产生
    # 比不掉的差异。
    op.create_index(
        "idx_fact_growth_rank", "fact_growth",
        ["site", "anchor_date", "rank_gain"], schema=SCHEMA,
    )

    # ---- 权限点 ----
    code, name = _PERMISSION
    op.execute(
        sa.text(
            f"INSERT INTO {SCHEMA}.permission (code, name) VALUES (:code, :name) "
            f"ON CONFLICT (code) DO NOTHING"
        ).bindparams(code=code, name=name)
    )
    roles_sql = ", ".join(f"'{r}'" for r in _GRANTED_TO)
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.role_permission (role_id, permission_id)
        SELECT r.id, p.id
          FROM {SCHEMA}.role r
          JOIN {SCHEMA}.permission p ON p.code = '{code}'
         WHERE r.code IN ({roles_sql})
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    """回滚不丢原始事实——榜单是可从 fact_daily 重算的派生数据。"""
    code, _ = _PERMISSION
    # 先断关联再删本体。外键是 ON DELETE CASCADE，显式先删是为了让「这次动了哪两张表」
    # 在迁移里看得见，而不是依赖读者记得那条级联。
    op.execute(
        f"""
        DELETE FROM {SCHEMA}.role_permission
         WHERE permission_id IN (SELECT id FROM {SCHEMA}.permission WHERE code = '{code}')
        """
    )
    op.execute(f"DELETE FROM {SCHEMA}.permission WHERE code = '{code}'")
    # DROP TABLE 连带其所有月度分区。
    op.drop_table("fact_growth", schema=SCHEMA)
