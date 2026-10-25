"""关键词品牌命中对（keyword_brand_hit）与切片就绪标记（site_data_stats.brand_hits_ready）

Revision ID: b7d1e4a9c2f3
Revises: 8f2a6c4e1b93
Create Date: 2026-10-26

#154 阶段二 D4：排除品牌词从逐行子串 + 正则改为按 (关键词, 品牌) 主键反连接。

**不在迁移里回填。** 命中对要对保留窗口内每个切片的前三品牌槽位逐行判定一次（US 周报单切片
约 14 s），全窗口不该放进迁移事务。迁移只建空表、加默认为假的就绪列（常量默认值，PG11+ 不重写
表）；就绪为假的切片照旧逐行判定，结果相同。回填见 ``scripts/backfill_brand_hits.py``，新导入
在收尾时自己算。``downgrade`` 删列删表。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from config import settings

revision: str = "b7d1e4a9c2f3"
down_revision: str | None = "8f2a6c4e1b93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA


def upgrade() -> None:
    op.create_table(
        "keyword_brand_hit",
        sa.Column("keyword_id", sa.BigInteger, primary_key=True, comment="→ dim_keyword"),
        sa.Column("brand_id", sa.Integer, primary_key=True, comment="→ dim_brand"),
        schema=SCHEMA,
        comment="关键词以完整单词包含该品牌名的 (关键词, 品牌) 对；排除品牌词筛选用。",
    )
    op.add_column(
        "site_data_stats",
        sa.Column("brand_hits_ready", sa.Boolean, nullable=False, server_default=sa.text("false"),
                  comment="本切片的品牌命中对是否已算入 keyword_brand_hit；为假时排除品牌词逐行判定"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("site_data_stats", "brand_hits_ready", schema=SCHEMA)
    op.drop_table("keyword_brand_hit", schema=SCHEMA)
