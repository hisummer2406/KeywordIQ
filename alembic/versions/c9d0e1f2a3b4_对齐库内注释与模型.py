"""对齐库内注释与模型

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-08-12

**这条迁移不改结构，只改注释**——但它修的不是"文档不好看"，而是一个持续制造噪音的源头。

alembic 的 autogenerate 会比对列注释与表注释。模型里的文本与库里的差一个字，就多一条
``alter_column``。改这条之前，一次 autogenerate 会吐出约 40 条纯注释差异（库里的文本是
建表迁移用集中的 ``COMMENT ON`` 块写的，比模型里的详细），**而真正的结构差异就淹在里面**：
``user_center.created_at/updated_at`` 在模型里是无时区 ``DateTime``、库里是 ``timestamptz``,
这条一直混在四十行注释 diff 中间，没人看得见。

对齐方向按"哪份文本更好"逐项定，不搞一刀切：

- 库里更详细的（站点码的 GB/UK 说明、is_new 的索引提示、Top 商品槽位的名次含义……）
  **抄进模型**，不在这条迁移里出现。
- 模型里更准或库里缺失的**在这里写进库**，就是下面这些。

此后模型是唯一事实来源：改模型注释就要配一条迁移，否则下一次 autogenerate 会替你生成。
共用文本收在 ``app/table/analysis/column_comments.py``。
"""
from collections.abc import Sequence

from alembic import op

from config import settings

revision: str = "c9d0e1f2a3b4"
down_revision: str | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA

_SITE = "ISO 3166-1 alpha-2 大写站点码（英国用 GB 不用 UK），取值由应用层站点清单约束"
_PERIOD = "周期类型：DAY 或 WEEK"

# (表, 列, 新注释)。分区父表上的 COMMENT 不需要逐个分区重复。
COLUMN_COMMENTS: tuple[tuple[str, str, str], ...] = (
    # 品牌/类目六列由 d3e4f5a6b7c8 加入时没写注释，模型里一直有。
    ("fact_daily", "top1_brand_id", "→ dim_brand"),
    ("fact_daily", "top1_category_id", "→ dim_category"),
    ("fact_weekly", "top1_brand_id", "→ dim_brand"),
    ("fact_weekly", "top1_category_id", "→ dim_category"),
    # 库里是通用的「报告日期」，周报这列的粒度是「所属周」，模型的说法更准。
    ("fact_weekly", "report_date_week", "所属周的报告日期（一级 RANGE 分区键）"),
    # 有了 dropped_count 之后，「事实行数」不再足以区分在榜与掉榜。
    ("site_data_stats", "row_count", "在榜行数（该站点/周期/期次），供分页总数直接读取"),
    # 掉榜表建表时用的是简写，统一到与其余五张表一致的措辞。
    ("fact_dropped", "site", _SITE),
    ("fact_dropped", "period_type", _PERIOD),
    # RBAC 三列建表时没写注释。
    ("permission", "code", "如 analysis:view"),
    ("role", "code", "如 admin/operator/analyst"),
    ("role", "is_system", "内置角色，禁止删除"),
)

TABLE_COMMENTS: tuple[tuple[str, str], ...] = (
    ("site_data_stats", "按 (站点,周期,期次) 的行数统计，服务分页总数与日期选择器；导入结束时写入。"),
)


def upgrade() -> None:
    for table, column, comment in COLUMN_COMMENTS:
        op.execute(f"COMMENT ON COLUMN {SCHEMA}.{table}.{column} IS '{comment}'")
    for table, comment in TABLE_COMMENTS:
        op.execute(f"COMMENT ON TABLE {SCHEMA}.{table} IS '{comment}'")


def downgrade() -> None:
    """回到对齐前的文本。注释不承载数据，回滚只是为了让链条可逆。"""
    previous_columns = (
        ("fact_daily", "top1_brand_id", None),
        ("fact_daily", "top1_category_id", None),
        ("fact_weekly", "top1_brand_id", None),
        ("fact_weekly", "top1_category_id", None),
        ("fact_weekly", "report_date_week", "报告日期（一级 RANGE 分区键）"),
        ("site_data_stats", "row_count", "该 (站点,周期,期次) 的事实行数，供分页总数直接读取"),
        ("fact_dropped", "site", "ISO alpha-2 站点码"),
        ("fact_dropped", "period_type", "DAY 或 WEEK"),
        ("permission", "code", None),
        ("role", "code", None),
        ("role", "is_system", None),
    )
    for table, column, comment in previous_columns:
        literal = "NULL" if comment is None else f"'{comment}'"
        op.execute(f"COMMENT ON COLUMN {SCHEMA}.{table}.{column} IS {literal}")

    op.execute(
        f"COMMENT ON TABLE {SCHEMA}.site_data_stats IS "
        "'按 (站点,周期,期次) 的行数统计。同时服务两个用途：分页总数 O(1) 读取，"
        "以及日期选择器的可选日期列表。由导入链路在每次导入结束时写入。'"
    )
