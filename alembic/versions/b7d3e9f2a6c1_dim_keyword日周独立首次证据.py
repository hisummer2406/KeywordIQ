"""dim_keyword 增加日报、周报各自的永久首次证据

Revision ID: b7d3e9f2a6c1
Revises: b8b59f9e172f
Create Date: 2026-11-10

issue #178，见 openspec/changes/add-period-first-listing（design D1、D2）。

共享首次记录（``first_seen_*``）是同站点日周共享的「首次进入监测」；本迁移加的是按周期独立的
「首次进入日榜 / 周榜」证据，二者互不覆盖。等级多一个 ``ABSENT``（已确认从未出现在该周期），
由导入在新建词时给另一周期写入，或由证据完整的回填写入。

**既有关键词两周期一律 ``UNKNOWN``、日期为空**，与 3c8e5b1d7f42 同一理由：迁移日不是任何词的
首次。能证明的值由 ``scripts/backfill_period_first.py`` 按事实重放写入，不放进迁移事务。

默认值是常量，PG11+ 加列不重写表；两个部分索引只收 ``IMPORTED``，迁移时为空，建索引是一次
顺序扫描，会短暂锁写 ``dim_keyword``（alembic 事务内不能 CONCURRENTLY），应在导入空闲时执行。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from config import settings

revision: str = "b7d3e9f2a6c1"
down_revision: str | None = "b8b59f9e172f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA

_BASIS_COMMENT = (
    "该周期首次依据：IMPORTED 本系统导入创建 / EARLIEST_OBSERVED 最早可观测(此前可能见过)"
    " / UNKNOWN 无证据 / ABSENT 已确认从未出现在该周期"
)
_PERIODS = (
    ("day", "首次出现在本站点日报的期次日期；等级为 UNKNOWN 或 ABSENT 时为空"),
    ("week", "首次出现在本站点周报的期次日期；等级为 UNKNOWN 或 ABSENT 时为空"),
)


def upgrade() -> None:
    for prefix, date_comment in _PERIODS:
        op.add_column(
            "dim_keyword",
            sa.Column(f"{prefix}_first_date", sa.Date, nullable=True, comment=date_comment),
            schema=SCHEMA,
        )
        op.add_column(
            "dim_keyword",
            sa.Column(f"{prefix}_first_basis", sa.String(20), nullable=False,
                      server_default="UNKNOWN", comment=_BASIS_COMMENT),
            schema=SCHEMA,
        )
        op.create_index(f"idx_dim_keyword_{prefix}_first", "dim_keyword",
                        ["site", f"{prefix}_first_date"], schema=SCHEMA,
                        postgresql_where=sa.text(f"{prefix}_first_basis = 'IMPORTED'"))


def downgrade() -> None:
    for prefix, _ in _PERIODS:
        op.drop_index(f"idx_dim_keyword_{prefix}_first", table_name="dim_keyword", schema=SCHEMA)
        op.drop_column("dim_keyword", f"{prefix}_first_basis", schema=SCHEMA)
        op.drop_column("dim_keyword", f"{prefix}_first_date", schema=SCHEMA)
