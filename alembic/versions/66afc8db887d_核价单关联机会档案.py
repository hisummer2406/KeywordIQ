"""核价单关联机会档案

Revision ID: 66afc8db887d
Revises: 4b219decb93a
Create Date: 2026-11-12

change add-opportunity-feasibility（Refs #171、#172）。

- ``pricing_sheet`` 加可空列 ``dossier_id``（FK → ``opp_dossier.id``，``ON DELETE SET NULL``）与部分索引：
  档案向团队展示关联核价单的测算摘要，核价单本身仍按创建人私有。
- ``opp_dossier_decision`` 加可空列 ``pricing_status``：签署决定时固化的测算摘要；此前的决定为空。
- 不改动事实表、分区或物化视图。downgrade 删除两列。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from config import settings

revision: str = "66afc8db887d"
down_revision: str | None = "4b219decb93a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA
_FK = "pricing_sheet_dossier_id_fkey"


def upgrade() -> None:
    op.add_column("opp_dossier_decision", sa.Column(
        "pricing_status", postgresql.JSONB(astext_type=sa.Text()), nullable=True,
        comment="签署时关联核价单的测算摘要（change add-opportunity-feasibility；此前的决定为空）"), schema=SCHEMA)
    op.add_column("pricing_sheet", sa.Column(
        "dossier_id", sa.BigInteger(), nullable=True,
        comment="关联的机会档案（change add-opportunity-feasibility）；关联后档案向团队展示测算摘要，明细仍私有"),
        schema=SCHEMA)
    op.create_index("idx_pricing_sheet_dossier", "pricing_sheet", ["dossier_id"], unique=False, schema=SCHEMA,
                    postgresql_where=sa.text("dossier_id IS NOT NULL"))
    op.create_foreign_key(_FK, "pricing_sheet", "opp_dossier", ["dossier_id"], ["id"],
                          source_schema=SCHEMA, referent_schema=SCHEMA, ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint(_FK, "pricing_sheet", schema=SCHEMA, type_="foreignkey")
    op.drop_index("idx_pricing_sheet_dossier", table_name="pricing_sheet", schema=SCHEMA,
                  postgresql_where=sa.text("dossier_id IS NOT NULL"))
    op.drop_column("pricing_sheet", "dossier_id", schema=SCHEMA)
    op.drop_column("opp_dossier_decision", "pricing_status", schema=SCHEMA)
