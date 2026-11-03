"""独立站拉取租约：全局同一时刻只拉一个站点

Revision ID: d7a3b5c9e2f1
Revises: c4e8f1a2d6b9
Create Date: 2026-11-04

change serialize-dtc-fetch（Refs #120）D4。``competitor_store`` 加可空列
``fetch_lease_until``：PG 11+ 加可空列不重写表，89 行。不做数据修复。``downgrade`` 删列。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from config import settings

revision: str = "d7a3b5c9e2f1"
down_revision: str | None = "c4e8f1a2d6b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA


def upgrade() -> None:
    op.add_column(
        "competitor_store",
        sa.Column(
            "fetch_lease_until",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=(
                "拉取租约到期时刻：非空且晚于当前即「正在拉取」。全局同一时刻最多一个站点持有租约，"
                "落库时清除；进程被杀留下的租约到期后自然失效"
            ),
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("competitor_store", "fetch_lease_until", schema=SCHEMA)
