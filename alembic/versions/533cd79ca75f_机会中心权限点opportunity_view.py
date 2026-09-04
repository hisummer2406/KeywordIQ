"""机会中心权限点 opportunity:view

Revision ID: 533cd79ca75f
Revises: 7b8b046c075e
Create Date: 2026-09-04

issue #76：为"机会中心"一级菜单预置权限点。与既有权限点（如 `a32f4b9a7841` 的
`competitor:view`）不同的一点：这次**不**把新权限点授予任何内置角色。issue 明确
"默认只授予超管，其他内置角色不自动获得"——这里的"超管"是 `is_super` 这个布尔位，
不是 `admin` 角色码，二者是独立字段（见 `app/user/user_crud.py` 的
`create_user`："显式指定角色优先，否则才按 is_super 赋默认角色"）。`require_permission`
对 `is_super=True` 的用户无条件放行，因此不写 `role_permission` 就已经是"仅超管可见"，
不需要额外的判断逻辑。

因此本迁移只 `INSERT INTO permission`，**不**做 `a32f4b9a7841` 那样按角色批量授予的
第二段 INSERT——不是遗漏，是这个权限点的默认状态就该是零授予。将来若要"指定角色试用"
（issue 里描述的第二步），需要新写一条迁移显式 `INSERT INTO role_permission`，并同步
把 `app/auth/permissions.py` 的 `OPPORTUNITY_VIEW` 从 `SUPER_ONLY_PERMISSIONS` 移出。

与 `app/auth/permissions.py` 的矩阵保持一致。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from config import settings

# revision identifiers, used by Alembic.
revision: str = "533cd79ca75f"
down_revision: str | None = "7b8b046c075e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA

_PERMISSION = ("opportunity:view", "查看机会中心")


def upgrade() -> None:
    code, name = _PERMISSION
    op.execute(
        sa.text(
            f"INSERT INTO {SCHEMA}.permission (code, name) VALUES (:code, :name) "
            f"ON CONFLICT (code) DO NOTHING"
        ).bindparams(code=code, name=name)
    )
    # 有意不写 role_permission：默认零授予，仅 is_super 可见，见本文件顶部 docstring。


def downgrade() -> None:
    code, _ = _PERMISSION
    op.execute(
        f"""
        DELETE FROM {SCHEMA}.role_permission
         WHERE permission_id IN (SELECT id FROM {SCHEMA}.permission WHERE code = '{code}')
        """
    )
    op.execute(f"DELETE FROM {SCHEMA}.permission WHERE code = '{code}'")
