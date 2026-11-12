"""核查详情增加全部附图与公报 PDF

Revision ID: c4e8a1d2f6b9
Revises: 3b918ff1ccc9
Create Date: 2026-11-12

change add-patent-fulltext-evidence（Refs #24）。

- ``risk_patent_detail.kind`` 的检查约束增加 ``drawings``、``pdf``。文件本体存 OSS，清单放在已有的
  ``data`` JSONB 里，不新建表。
- 同步相关列与表注释。
- downgrade 先删除新种类的详情行（文件清单随之丢失，OSS 对象不删），再恢复原约束。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from config import settings

revision: str = "c4e8a1d2f6b9"
down_revision: str | None = "3b918ff1ccc9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA
_CK = "ck_risk_patent_detail_kind"


def _comments(*, new: bool) -> None:
    op.alter_column(
        "risk_task", "kind", schema=SCHEMA, existing_type=sa.String(16), existing_nullable=False,
        comment="search（检索一页）/detail（专利详情）" if new else "search（检索一页）/detail（著录与法律状态）",
    )
    op.alter_column(
        "risk_provider_call", "api", schema=SCHEMA, existing_type=sa.String(8), existing_nullable=False,
        comment="P010/P060/P011/P013/P042/P020" if new else "P010/P060/P011/P013",
    )
    op.alter_column(
        "risk_patent_detail", "kind", schema=SCHEMA, existing_type=sa.String(16), existing_nullable=False,
        comment="bibliography/legal/drawings/pdf" if new else "bibliography/legal",
    )
    op.alter_column(
        "risk_patent_detail", "data", schema=SCHEMA, existing_nullable=True,
        existing_type=postgresql.JSONB(),
        comment="规范化字段 + 原始条目；附图与公报含存档清单" if new else "规范化字段 + 原始条目",
    )
    op.create_table_comment(
        "risk_patent_detail",
        "候选专利的著录、法律状态、全部附图与公报" if new else "候选专利的著录与法律状态",
        schema=SCHEMA,
    )


def upgrade() -> None:
    op.drop_constraint(_CK, "risk_patent_detail", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        _CK, "risk_patent_detail", "kind IN ('bibliography', 'legal', 'drawings', 'pdf')", schema=SCHEMA,
    )
    _comments(new=True)


def downgrade() -> None:
    op.execute(f"DELETE FROM {SCHEMA}.risk_patent_detail WHERE kind IN ('drawings', 'pdf')")
    op.drop_constraint(_CK, "risk_patent_detail", schema=SCHEMA, type_="check")
    op.create_check_constraint(_CK, "risk_patent_detail", "kind IN ('bibliography', 'legal')", schema=SCHEMA)
    _comments(new=False)
