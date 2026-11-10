"""竞对卖家店名：上报顺带的公开展示属性

Revision ID: e8b2c6d4f1a3
Revises: 140193107685
Create Date: 2026-11-04

change refocus-competitor-product-discovery（Refs #166）D5。``competitor_seller`` 加可空列
``seller_name``：PG 11+ 加无默认值的可空列只改元数据、不重写表。不回填（历史上从未采过店名，
界面回落显示 seller_id）。``downgrade`` 删列，只丢失店名这一展示属性。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from config import settings

revision: str = "e8b2c6d4f1a3"
down_revision: str | None = "140193107685"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA

# 与模型共用同一份注释文本，见 seller_model.SELLER_NAME_COMMENT。
SELLER_NAME_COMMENT = (
    "卖家店名：插件从公开店铺页尽力解析后随上报带来，非重放且非空时按最新值覆盖；"
    "NULL 表示从未取到，界面回落显示 seller_id。不参与任何判定"
)


def upgrade() -> None:
    op.add_column(
        "competitor_seller",
        sa.Column("seller_name", sa.String(200), nullable=True, comment=SELLER_NAME_COMMENT),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("competitor_seller", "seller_name", schema=SCHEMA)
