"""移除 user:manage 与 role:manage 两个权限点

Revision ID: d0e1f2a3b4c5
Revises: a7b8c9d0e1f2
Create Date: 2026-08-12

用户管理收紧为 `is_super` 专属（`simple_auth.require_super()`），不再走权限点：

- **`user:manage`**：收紧后它变成一个「勾了也不生效」的权限——角色编辑界面会让人以为
  把它授予运营，运营就能管用户，实际端点只认 `is_super`。一个可授予但无效的权限点比
  没有这个权限点更糟，故删除。
- **`role:manage`**：自 `c2d3e4f5a6b7` 种入以来，前后端没有任何代码读过它，是纯粹的
  死配置，顺手一起清掉。

普通表的数据删除，无分区/物化视图，可手写。`role_permission.permission_id` 的外键是
`ON DELETE CASCADE`，只删 `permission` 本体关联行也会跟着走；这里仍显式先删关联，是为了
让「这次动了哪两张表」在迁移里看得见，而不是依赖读者记得那条级联。

**降级会把两个权限点连同 admin 的关联一并恢复**，与 `c2d3e4f5a6b7` 的种子一致；
但降级**不会**把 `user_api` 的依赖改回 `require_permission`，代码与库需一并回退。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "analysis"

# 与 c2d3e4f5a6b7 种子里的写法一致，降级时原样恢复
_DROPPED = [
    ("user:manage", "用户管理"),
    ("role:manage", "角色与权限管理"),
]
# 这两个权限当初只授予了 admin 角色
_HELD_BY = "admin"


def upgrade() -> None:
    codes = [c for c, _ in _DROPPED]
    bind = op.get_bind()

    # 先断关联再删本体，否则撞 role_permission 的外键
    bind.execute(
        sa.text(
            f"""
            DELETE FROM {SCHEMA}.role_permission
             WHERE permission_id IN (
                   SELECT id FROM {SCHEMA}.permission WHERE code = ANY(:codes)
             )
            """
        ),
        {"codes": codes},
    )
    bind.execute(
        sa.text(f"DELETE FROM {SCHEMA}.permission WHERE code = ANY(:codes)"),
        {"codes": codes},
    )


def downgrade() -> None:
    bind = op.get_bind()
    for code, name in _DROPPED:
        # ON CONFLICT：降级可能跑在权限点仍在的库上（例如只回退了代码）
        bind.execute(
            sa.text(
                f"""
                INSERT INTO {SCHEMA}.permission (code, name)
                VALUES (:code, :name)
                ON CONFLICT (code) DO NOTHING
                """
            ),
            {"code": code, "name": name},
        )
        bind.execute(
            sa.text(
                f"""
                INSERT INTO {SCHEMA}.role_permission (role_id, permission_id)
                SELECT r.id, p.id
                  FROM {SCHEMA}.role r, {SCHEMA}.permission p
                 WHERE r.code = :role AND p.code = :code
                ON CONFLICT DO NOTHING
                """
            ),
            {"role": _HELD_BY, "code": code},
        )
