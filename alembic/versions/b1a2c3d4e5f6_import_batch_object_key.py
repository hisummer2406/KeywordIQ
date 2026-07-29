"""import_batch 增加 object_key（OSS-first 溯源）

Revision ID: b1a2c3d4e5f6
Revises: fa4c61e31f16
Create Date: 2026-07-27

OSS-first 导入流程：表格文件先直传 OSS，导入以 OSS 对象为源。批次记录增加
``object_key`` 以持久化原始文件在 OSS 的位置，供溯源与重导。本地文件退路时为空串。

普通列增删，非分区/物化视图，可安全 autogenerate 风格手写。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b1a2c3d4e5f6"
down_revision: Union[str, None] = "fa4c61e31f16"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "analysis"


def upgrade() -> None:
    op.add_column(
        "import_batch",
        sa.Column(
            "object_key",
            sa.String(1024),
            nullable=False,
            server_default="",
            comment="原始文件的 OSS object key",
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("import_batch", "object_key", schema=SCHEMA)
