"""import_batch 增加事实提交时刻（facts_committed_at）

Revision ID: b7d2e9a4c1f6
Revises: 66afc8db887d
Create Date: 2026-11-25

issue #209 P1：归档闸门原先按 id 最大的成功批次判定一期数据的原始文件，但 id 在上传时
分配，事实提交顺序可以相反；收尾中断后被补完的批次还会晚于真正覆盖数据的批次标成成功。
本列与事实在同一事务写入，取值最新的批次就是当前事实的实际写入者。

**不回填**：迁移前的批次无从确认谁最后写入（见上），写一个猜测值反而会把不确定伪装成
确定。归档闸门对没有提交记录的期次改为校验全部可能的写入批次（见 retention_service）。

可空列，PG11+ 加列不重写表；`import_batch` 很小。`downgrade` 只删本列。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from config import settings

revision: str = "b7d2e9a4c1f6"
down_revision: str | None = "66afc8db887d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA


def upgrade() -> None:
    op.add_column(
        "import_batch",
        sa.Column("facts_committed_at", sa.DateTime(timezone=True), nullable=True,
                  comment="事实与本批次计数同一事务提交的时刻；同期次取值最新者即当前事实的写入批次，NULL 为未写入或迁移前批次"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("import_batch", "facts_committed_at", schema=SCHEMA)
