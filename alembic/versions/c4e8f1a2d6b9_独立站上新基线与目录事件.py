"""独立站上新基线改为首次完整目录、新增商品目录事件表、修复被污染的历史数据

Revision ID: c4e8f1a2d6b9
Revises: b7d1e4a9c2f3
Create Date: 2026-11-03

change fix-dtc-catalog-collection（Refs #120）D3/D4/D6。

1. ``competitor_store.first_fetch_at`` 更名 ``baseline_at``：原值在首次**尝试**时落定、
   不论完整与否，生产 41,105 个存量商品因此被报成上新。
2. 新建只追加的 ``competitor_store_product_event``（下架 / 重新出现）。
3. 数据修复，按用户决定「现有商品全部算基线」：
   - 目录当前完整的站点 ``baseline_at = now()``；其余置 NULL，等各自首次完整拉取落定
     （届时库里已有商品同样全部算基线）。上新流因此在迁移后清空、此后重新累积。
   - 被 429 限流误判成「未开放商品接口」的站点恢复为可拉取，并让下一轮优先重拉。
   - 已下架商品各补一条 DELISTED 事件（``occurred_at = delisted_at``）。

站点表 89 行、补事件约 3,600 行，迁移内执行没有锁与时长问题。

**downgrade 只还原结构**：删事件表、列名改回 ``first_fetch_at``。第 3 步的数据改写不可逆
——原值本身是错的，也没有留底；回滚后旧代码按改写后的基线工作，不会再产生假上新。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from config import settings

revision: str = "c4e8f1a2d6b9"
down_revision: str | None = "b7d1e4a9c2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA

DOMAIN_COMMENT = "规范化后的站点域名（去协议/www/端口/路径），如 leteimall.com"
BASELINE_COMMENT = (
    "上新基线：首次**完整**拿到目录的时刻，落定后不再改。上新流判据为 "
    "first_seen_at > baseline_at；NULL 表示尚未完整拉取过，此时不产生任何上新"
)
OLD_FIRST_FETCH_COMMENT = (
    "首刷时刻。**上新流的判据**：first_seen_at > first_fetch_at 才是上新——"
    "首刷那批商品是「我们刚开始看」不是「今天上新」，没有这一列就无从区分"
)


# 数据修复语句。单独成常量是为了让测试在回滚事务里直接执行它们、核对修复结果，而不必在
# 共享测试库上来回 downgrade/upgrade（并行会话的集成测试会被打断）。
REPAIR_SQL: tuple[str, ...] = (
    # 「现有商品全部算基线」：完整的站点基线即迁移时刻，其余等首次完整拉取落定。
    f"""
    UPDATE {SCHEMA}.competitor_store
       SET baseline_at = CASE WHEN catalog_complete THEN now() ELSE NULL END
    """,
    # 被 429 误判成「未开放商品接口」的站点：恢复可拉取，置空 last_fetch_at 让下一轮优先重拉。
    f"""
    UPDATE {SCHEMA}.competitor_store
       SET products_json_ok = true,
           last_fetch_status = 'FAILED',
           last_fetch_at = NULL
     WHERE NOT products_json_ok
       AND last_error ILIKE '%429%'
    """,
    # 已下架商品补一条下架事件，让事件表从一开始就与 delisted_at 一致。
    f"""
    INSERT INTO {SCHEMA}.competitor_store_product_event (domain, product_id, occurred_at, kind)
    SELECT domain, product_id, delisted_at, 'DELISTED'
      FROM {SCHEMA}.competitor_store_product
     WHERE delisted_at IS NOT NULL
    ON CONFLICT DO NOTHING
    """,
)


def upgrade() -> None:
    op.alter_column(
        "competitor_store",
        "first_fetch_at",
        new_column_name="baseline_at",
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=True,
        comment=BASELINE_COMMENT,
        schema=SCHEMA,
    )

    op.create_table(
        "competitor_store_product_event",
        sa.Column("domain", sa.String(255), nullable=False, comment=DOMAIN_COMMENT),
        sa.Column(
            "product_id",
            sa.BigInteger,
            nullable=False,
            comment="→ competitor_store_product.product_id",
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="判定发生的时刻（即那次拉取的时刻）",
        ),
        sa.Column(
            "kind",
            sa.String(16),
            nullable=False,
            comment="DELISTED = 完整拉取中消失被判下架；RELISTED = 已下架商品再次出现",
        ),
        sa.PrimaryKeyConstraint(
            "domain", "product_id", "occurred_at", "kind", name="pk_competitor_store_product_event"
        ),
        sa.ForeignKeyConstraint(
            ["domain"],
            [f"{SCHEMA}.competitor_store.domain"],
            name="fk_competitor_store_product_event_store",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
        comment=(
            "独立站商品目录事件：下架与重新出现，只追加；"
            "当前状态见 competitor_store_product.delisted_at。"
        ),
    )
    op.create_index(
        "idx_competitor_store_product_event_time",
        "competitor_store_product_event",
        ["domain", "occurred_at"],
        schema=SCHEMA,
    )

    # ---- 数据修复（不可逆，见模块文档） ----
    for statement in REPAIR_SQL:
        op.execute(statement)


def downgrade() -> None:
    op.drop_index(
        "idx_competitor_store_product_event_time",
        table_name="competitor_store_product_event",
        schema=SCHEMA,
    )
    op.drop_table("competitor_store_product_event", schema=SCHEMA)
    op.alter_column(
        "competitor_store",
        "baseline_at",
        new_column_name="first_fetch_at",
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=True,
        comment=OLD_FIRST_FETCH_COMMENT,
        schema=SCHEMA,
    )
