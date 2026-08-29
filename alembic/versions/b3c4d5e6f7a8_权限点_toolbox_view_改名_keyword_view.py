"""权限点 toolbox:view 改名 keyword:view

Revision ID: b3c4d5e6f7a8
Revises: a32f4b9a7841
Create Date: 2026-08-29

「工具箱」这个按排除法命名的一级菜单被拆掉（见变更 `restructure-top-nav`）：外观专利与
全球商标库拆去新的「侵权查询」菜单且**不再由任何权限点把守**（它们没有后端端点，罩着
它们的权限点对它们不生效）；持续增长市场与我的词库留在改名后的「关键词工具」菜单下，
权限点随之改名。

**这是 `analysis.permission` 上的一行 UPDATE，没有 DDL。** `role_permission` 按
`permission.id` 关联（见 `app/user/rbac_model.py`），不是按 code，所以角色的授予关系
一行都不用动——**没有任何用户的可见范围因此改变**。看到这条迁移没碰 `role_permission`
不是漏了。

降级是反向的同一行 UPDATE，完全可逆。

迁移不 import 应用代码（沿用既有约定），code 与中文名在这里硬编码，与
`app/auth/permissions.py` 的矩阵两处同步。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from config import settings

revision: str = "b3c4d5e6f7a8"
down_revision: str | None = "a32f4b9a7841"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA

_OLD = ("toolbox:view", "查看工具箱")
_NEW = ("keyword:view", "查看关键词工具")


def _rename(from_code: str, to_code: str, to_name: str) -> None:
    op.execute(
        sa.text(
            f"UPDATE {SCHEMA}.permission SET code = :to_code, name = :to_name "
            "WHERE code = :from_code"
        ).bindparams(to_code=to_code, to_name=to_name, from_code=from_code)
    )


def upgrade() -> None:
    _rename(_OLD[0], _NEW[0], _NEW[1])


def downgrade() -> None:
    _rename(_NEW[0], _OLD[0], _OLD[1])
