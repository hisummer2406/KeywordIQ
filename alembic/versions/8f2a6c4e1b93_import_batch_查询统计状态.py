"""import_batch 增加查询统计状态（stats_status / stats_attempts）

Revision ID: 8f2a6c4e1b93
Revises: 3c8e5b1d7f42
Create Date: 2026-10-22

issue #148，见 openspec/changes/post-import-query-readiness。

导入现在会在期次可见前对写入的叶子分区执行 ANALYZE；失败不回滚数据，靠这两列让失败可见、
可被导入轮询重试。

**不回填**：迁移前的批次 `stats_status` 为 NULL，语义就是「不适用」——它们导入时没有这一步，
写成 READY 是撒谎，写成 PENDING 会让重试任务对着几百个旧批次挨个 ANALYZE。

两列都是可空或常量默认值，PG11+ 加列不重写表；`import_batch` 很小，锁写时间可忽略。
`downgrade` 只删本迁移加的两列。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from config import settings

revision: str = "8f2a6c4e1b93"
down_revision: str | None = "3c8e5b1d7f42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA


def upgrade() -> None:
    op.add_column(
        "import_batch",
        sa.Column("stats_status", sa.String(16), nullable=True,
                  comment="查询统计状态：PENDING/READY/FAILED；NULL 为不适用（旧批次或未成功导入）"),
        schema=SCHEMA,
    )
    op.add_column(
        "import_batch",
        sa.Column("stats_attempts", sa.SmallInteger, nullable=False, server_default="0",
                  comment="查询统计刷新已失败的次数，达到上限后置 FAILED 不再重试"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("import_batch", "stats_attempts", schema=SCHEMA)
    op.drop_column("import_batch", "stats_status", schema=SCHEMA)
