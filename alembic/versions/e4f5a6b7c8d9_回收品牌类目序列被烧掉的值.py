"""回收 dim_brand / dim_category 序列被 ON CONFLICT 烧掉的值

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-08-05

配套的代码修复在 ``importer._upsert_dimensions``：给维度 INSERT 加了 NOT EXISTS 预筛，
使 ``nextval`` 只对真正的新名称发生。本迁移处理的是**修复之前已经烧掉的那些值**。

背景：PostgreSQL 对 ``INSERT ... ON CONFLICT DO NOTHING`` 是先取默认值（``nextval``）
再判冲突，所以哪怕一行都没插进去，序列照样按候选行数前进。每次导入约 120 个类目、
3 个站点每天各一次日报，约 400/天；而 ``dim_category.category_id`` 是 smallserial，
上限只有 32767（选 smallint 是为了省事实表上 3 列 × 2 亿行的空间）——约 78 天耗尽，
此后每次导入直接 ``ERROR: nextval: reached maximum value of sequence``。

``dim_brand`` 的主键是 integer（上限 21 亿），按同样的速率约 13 年才会到顶，并不紧迫，
但它和类目共用同一段 INSERT，一并回收没有额外成本。

**为什么是 setval 而不是重建序列**：两张维度表的 id 只被事实表按值引用，没有外键，
也不存在"号段有语义"的约定。把序列拨回 ``max(id)`` 之后，接下来发放的号与既有行不
冲突（唯一约束仍在兜底），既有数据一行都不用动。

**幂等**：反复执行结果相同——它只是把序列对齐到当前最大 id。
"""
from collections.abc import Sequence

from alembic import op

from config import settings

revision: str = "e4f5a6b7c8d9"
down_revision: str | None = "d3e4f5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA

# (表名, 主键列名)
_DIMENSIONS = (("dim_brand", "brand_id"), ("dim_category", "category_id"))


def upgrade() -> None:
    for table, id_col in _DIMENSIONS:
        # 第三个参数 is_called：表里有行时置 true（下一个号是 max+1）；空表时置 false，
        # 让下一个号回到 1 而不是 2。写死 true 会在空库上白白跳过第一个号。
        op.execute(
            f"""
            SELECT setval(
                pg_get_serial_sequence('{SCHEMA}.{table}', '{id_col}'),
                COALESCE(MAX({id_col}), 1),
                MAX({id_col}) IS NOT NULL
            )
              FROM {SCHEMA}.{table}
            """
        )


def downgrade() -> None:
    """空操作，且这是刻意的。

    被烧掉的号本就没有任何数据引用（它们从未落到行上），"恢复"它们既不可能也没有
    意义。把序列往回拨到某个更大的值只会重新逼近上限，是纯粹的倒退。
    """
