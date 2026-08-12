"""批次状态新增 UPLOADING

Revision ID: e1f2a3b4c5d6
Revises: c9d0e1f2a3b4
Create Date: 2026-08-12

**只改一条列注释，不改结构。** `import_batch.status` 是 `String(16)`，没有 PG enum、
没有 CHECK 约束，取值由应用层的 `VALID_STATUSES` 约束——新增一个状态值因此不需要 DDL。

那为什么还要一条迁移：列注释参与 alembic 的 autogenerate 比对。模型里的注释补了新状态、
库里没补，下一次 autogenerate 就会生成一条 alter_column 出来。让这类差异常驻的代价不是
不好看，而是它会把真正的结构差异淹掉（见 c9d0e1f2a3b4 的说明）。

`UPLOADING` 是签发直传凭证时就落下的状态，早于导入被触发。它的收尾（超时后按对象是否
真实存在分流：在则转 QUEUED 交排队接手，不在则判 FAILED）在应用层，见
`import_service.reap_stale_uploads`，不涉及库结构。
"""
from collections.abc import Sequence

from alembic import op

from config import settings

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA

_NEW = "UPLOADING（直传中）/ QUEUED（排队）/ PROCESSING / COMPLETED / FAILED"
_OLD = "QUEUED（排队）/ PROCESSING / COMPLETED / FAILED"


def upgrade() -> None:
    op.execute(f"COMMENT ON COLUMN {SCHEMA}.import_batch.status IS '{_NEW}'")


def downgrade() -> None:
    """注释回退。**存量的 UPLOADING 行不动**——它们是真实发生过的上传尝试，
    改写成别的状态等于伪造记录；降级后应用层不再产生新的该状态值即可。"""
    op.execute(f"COMMENT ON COLUMN {SCHEMA}.import_batch.status IS '{_OLD}'")
