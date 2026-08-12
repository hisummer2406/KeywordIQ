"""删除空置的站点参考表

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-08-12

``analysis.sites`` 从建表起就没有任何写入路径——没有 seed、没有导入侧回写、没有管理
端点，库里始终是空表。它设想的用途（界面展示站点中文名/时区/域名）由
``app.site_registry.SITE_CATALOG`` 承担，界面经 ``GET /api/upload/sites`` 直接取那份
编译期常量，从未查过这张表。

**删掉而不是补 seed。** 站点码的唯一事实来源是 ``site_registry``（见 CLAUDE.md 的铁律）。
把同样三个字段再灌一份进库，就等于给站点元数据开了第二个事实来源，而两份副本迟早分叉
——``GB``/``UK`` 两种写法这类问题正是这么来的。现在的产品形态里站点是三个写死的枚举，
不是运营可配置的数据。

**数据风险为零**：表是空的，且没有任何外键指向它（当初就刻意不与事实表建外键）。
downgrade 能原样建回结构，但建回来的同样是一张空表——没有数据可还原，因为从来没有。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from config import settings

revision: str = "b8c9d0e1f2a3"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA


def upgrade() -> None:
    op.drop_table("sites", schema=SCHEMA)


def downgrade() -> None:
    """建回结构（空表），与 fa4c61e31f16 里的原始定义一致。"""
    op.create_table(
        "sites",
        sa.Column("code", sa.String(8), nullable=False,
                  comment="ISO 3166-1 alpha-2 大写站点码（英国用 GB 不用 UK），"
                          "取值由应用层站点清单约束"),
        sa.Column("name_zh", sa.String(64), nullable=False, server_default=""),
        sa.Column("timezone", sa.String(64), nullable=False, server_default=""),
        sa.Column("domain", sa.String(128), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("code"),
        schema=SCHEMA,
        comment="站点参考表：仅供界面展示中文名/时区/域名。站点码合法性由应用层 site_registry 保证，"
                "此表不与事实表建外键。",
    )
