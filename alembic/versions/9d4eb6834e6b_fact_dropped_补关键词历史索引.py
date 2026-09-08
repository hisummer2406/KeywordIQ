"""fact_dropped 补关键词历史索引

Revision ID: 9d4eb6834e6b
Revises: 533cd79ca75f
Create Date: 2026-09-08 13:04:00.548752

PR #87 Codex 审核（P1）：趋势详情的 `keyword_dropped_stmt` 走 `fact_dropped` 主键
`(site, period_type, anchor_date, keyword_id)`。一旦 `anchor_date` 是范围而非等值
（30 天窗口、更别提"全部可用历史"），`keyword_id` 就退化成扫描内 filter——代价随该
站点/视图/日期窗口内的掉榜行数增长，不随目标关键词的历史行数增长，违背 proposal 里
「查询只按单个 keyword_id 的历史索引取数，不扫全站榜单」的设计前提。

新增 `idx_fact_dropped_keyword_history (site, period_type, keyword_id, anchor_date)`：
把 keyword_id 放到 anchor_date 之前，让等值条件先把范围收窄到单个关键词，anchor_date
范围再在这个子集内扫描。仅新增索引，不改表结构，`fact_dropped` 的分区方案不受影响
——在父表（分区表）上建索引会被 PostgreSQL 递归应用到全部现有叶子分区，`partitioning.
ensure_fact_partitions` 之后新建的分区同样自动带有该索引，无需在这里或那个函数里
额外处理。
"""
from collections.abc import Sequence

from alembic import op
from config import settings

# revision identifiers, used by Alembic.
revision: str = '9d4eb6834e6b'
down_revision: str | None = '533cd79ca75f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA


def upgrade() -> None:
    op.create_index(
        "idx_fact_dropped_keyword_history", "fact_dropped",
        ["site", "period_type", "keyword_id", "anchor_date"], schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("idx_fact_dropped_keyword_history", table_name="fact_dropped", schema=SCHEMA)
