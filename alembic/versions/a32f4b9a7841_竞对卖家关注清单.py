"""竞对卖家关注清单

Revision ID: a32f4b9a7841
Revises: b2c3d4e5f6a7
Create Date: 2026-08-29

issue #56 的 P0：把「批量盯竞对上新」产品化的第一步，只落「运营在盯哪些卖家」这份清单，
不含任何 ASIN 级数据（浏览器插件/Keepa 等数据获取层留给后续变更）。

**两层模型**：`competitor_seller` 是全局共享的卖家主体（`(seller_id, site)` 复合主键，
不含账号属性——同一卖家被多个用户关注时仍是同一行）；`user_seller_group` /
`user_seller_watch` 是账号私有的分组与关注关系。`user_seller_watch` 用复合外键指向
`competitor_seller(seller_id, site)`，因为关注的是一个具体站点的卖家，不是跨站点的
抽象主体（已定案：多站点同卖家分开算，不合并成一个主体）。

**分组不绑定站点**，与词库的 `keyword_group`（绑定站点且不可改）不同——一个卖家的站点
已由 `competitor_seller` 唯一确定，分组只是用户自己的归类标签，允许跨站点。**删除分组
不删除关注关系**（`user_seller_watch.group_id` 外键 `ON DELETE SET NULL`），同样与词库
（删组连带删条目）不同：分组是标签，关注关系是"我在盯这个卖家"这件事本身，误删分组不该
连带取消关注。

`competitor_watch_preview` 是导入两步流（先预览、用户确认后才 commit）的暂存表，替代
引入 Redis——仓库没有这个依赖，为一个几分钟的预览缓存引入它不划算。过期行清理挂在
`import_queue.py` 已有的轮询循环上，不新开定时器。

`baseline_at` 是为后续数据获取层预留的列：本变更只写 NULL，状态恒为「待建立基线」。

同批种入权限点 `competitor:view`（不复用 `toolbox:view`——每个用户的关注池互相独立，
是独立数据域，与 `analysis:view` 等同一档次的授权粒度），授予 admin/operator/analyst
三个内置角色，仿 `f2a3b4c5d6e7`（工具箱权限点）的写法：迁移不 import 应用代码，按
`app/auth/permissions.py` 同一份矩阵硬编码。

四张表均为普通表，不分区、无物化视图，DDL 由 autogenerate 产出。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from config import settings

# revision identifiers, used by Alembic.
revision: str = "a32f4b9a7841"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA

# 与 app/auth/permissions.py 的矩阵保持一致
_PERMISSION = ("competitor:view", "查看竞对监控")
_GRANTED_TO = ("admin", "operator", "analyst")


def upgrade() -> None:
    op.create_table(
        "competitor_seller",
        sa.Column("seller_id", sa.String(length=24), nullable=False,
                  comment="亚马逊卖家 ID，形如 A1TZ946F8HZTNY"),
        sa.Column("site", sa.String(length=8), nullable=False,
                  comment="ISO 3166-1 alpha-2 大写站点码（英国用 GB 不用 UK），"
                          "取值由应用层站点清单约束"),
        sa.Column("baseline_at", sa.DateTime(timezone=True), nullable=True,
                  comment="基线建立时间，NULL 表示尚未建立（本变更恒为 NULL，由数据获取层写入）"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.PrimaryKeyConstraint("seller_id", "site", name="pk_competitor_seller"),
        schema=SCHEMA,
        comment="竞对卖家主体：全局共享，不含账号属性；关注关系见 user_seller_watch。",
    )

    op.create_table(
        "competitor_watch_preview",
        sa.Column("preview_token", sa.String(length=36), nullable=False, comment="UUID4"),
        sa.Column("user_id", sa.Integer(), nullable=False,
                  comment="→ user_center.id，预览的归属人；确认接口据此校验，非归属人一律 404"),
        sa.Column("parsed", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  comment="解析结果：new/existing/unparsable/groups 等，供 commit 落库"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False,
                  comment="过期时间，由轮询任务清理"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.ForeignKeyConstraint(["user_id"], [f"{SCHEMA}.user_center.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("preview_token"),
        schema=SCHEMA,
        comment="竞对导入两步流的预览暂存，替代 Redis；过期行由轮询任务清理。",
    )
    op.create_index("idx_competitor_watch_preview_expires", "competitor_watch_preview",
                     ["expires_at"], unique=False, schema=SCHEMA)

    op.create_table(
        "user_seller_group",
        sa.Column("group_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False,
                  comment="→ user_center.id，分组的唯一归属人"),
        sa.Column("name", sa.String(length=50), nullable=False, comment="分组名，同一用户下唯一"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.ForeignKeyConstraint(["user_id"], [f"{SCHEMA}.user_center.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("group_id"),
        sa.UniqueConstraint("user_id", "name", name="uq_user_seller_group_user_name"),
        schema=SCHEMA,
        comment="竞对卖家分组：用户自建归类标签，不绑定站点，删组不连带删除关注关系。",
    )
    op.create_index("idx_user_seller_group_owner", "user_seller_group", ["user_id"],
                     unique=False, schema=SCHEMA)

    op.create_table(
        "user_seller_watch",
        sa.Column("watch_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False,
                  comment="→ user_center.id，关注关系的唯一归属人"),
        sa.Column("seller_id", sa.String(length=24), nullable=False,
                  comment="→ competitor_seller.seller_id"),
        sa.Column("site", sa.String(length=8), nullable=False, comment="→ competitor_seller.site"),
        sa.Column("group_id", sa.BigInteger(), nullable=True,
                  comment="→ user_seller_group.group_id，NULL 表示未分组"),
        sa.Column("priority", sa.Integer(), server_default="1", nullable=False,
                  comment="关注度信号，导入时取书签重复收藏次数，默认 1"),
        sa.Column("note", sa.String(length=200), nullable=True, comment="用户备注"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.ForeignKeyConstraint(["group_id"], [f"{SCHEMA}.user_seller_group.group_id"],
                                 ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["seller_id", "site"],
            [f"{SCHEMA}.competitor_seller.seller_id", f"{SCHEMA}.competitor_seller.site"],
            name="fk_user_seller_watch_seller",
        ),
        sa.ForeignKeyConstraint(["user_id"], [f"{SCHEMA}.user_center.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("watch_id"),
        sa.UniqueConstraint("user_id", "seller_id", "site", name="uq_user_seller_watch_user_seller"),
        schema=SCHEMA,
        comment="竞对卖家关注关系：账号私有，UNIQUE(user_id, seller_id, site)。",
    )
    op.create_index("idx_user_seller_watch_owner", "user_seller_watch", ["user_id", "group_id"],
                     unique=False, schema=SCHEMA)

    # ---- 权限点 ----
    code, name = _PERMISSION
    op.execute(
        sa.text(
            f"INSERT INTO {SCHEMA}.permission (code, name) VALUES (:code, :name) "
            f"ON CONFLICT (code) DO NOTHING"
        ).bindparams(code=code, name=name)
    )
    roles_sql = ", ".join(f"'{r}'" for r in _GRANTED_TO)
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.role_permission (role_id, permission_id)
        SELECT r.id, p.id
          FROM {SCHEMA}.role r
          JOIN {SCHEMA}.permission p ON p.code = '{code}'
         WHERE r.code IN ({roles_sql})
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    """回滚不丢原始事实——本变更没有写入任何 fact_daily/fact_weekly/dim_* 数据。"""
    code, _ = _PERMISSION
    op.execute(
        f"""
        DELETE FROM {SCHEMA}.role_permission
         WHERE permission_id IN (SELECT id FROM {SCHEMA}.permission WHERE code = '{code}')
        """
    )
    op.execute(f"DELETE FROM {SCHEMA}.permission WHERE code = '{code}'")

    op.drop_index("idx_user_seller_watch_owner", table_name="user_seller_watch", schema=SCHEMA)
    op.drop_table("user_seller_watch", schema=SCHEMA)
    op.drop_index("idx_user_seller_group_owner", table_name="user_seller_group", schema=SCHEMA)
    op.drop_table("user_seller_group", schema=SCHEMA)
    op.drop_index("idx_competitor_watch_preview_expires", table_name="competitor_watch_preview",
                  schema=SCHEMA)
    op.drop_table("competitor_watch_preview", schema=SCHEMA)
    op.drop_table("competitor_seller", schema=SCHEMA)
