"""RBAC：角色/权限表 + user_center.role_id + 种子与回填

Revision ID: c2d3e4f5a6b7
Revises: b1a2c3d4e5f6
Create Date: 2026-07-29

在既有自建鉴权（user_center）上扩出 RBAC（design 决策 4/4b）：
- 新增 permission / role / role_permission 三表；
- user_center 增 role_id FK → role；
- 种子 5 权限 + 3 内置角色（admin/operator/analyst）+ 角色-权限映射；
- 回填：is_super=true → admin，is_super=false → operator（保住老用户上传能力）。

普通表 + 数据填充，非分区/物化视图，可手写。种子矩阵须与 app/auth/permissions.py 一致。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, None] = "b1a2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "analysis"

# 与 app/auth/permissions.py 的矩阵保持一致
_PERMISSIONS = [
    ("analysis:view", "查询分析数据"),
    ("analysis:export", "导出分析数据"),
    ("upload:import", "上传/导入数据"),
    ("user:manage", "用户管理"),
    ("role:manage", "角色与权限管理"),
]
_ROLES = [
    ("admin", "超级管理员"),
    ("operator", "运营"),
    ("analyst", "分析师"),
]
_ROLE_PERMS = {
    "admin": {"analysis:view", "analysis:export", "upload:import", "user:manage", "role:manage"},
    "operator": {"analysis:view", "analysis:export", "upload:import"},
    "analyst": {"analysis:view", "analysis:export"},
}


def upgrade() -> None:
    op.create_table(
        "permission",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("code", name="uq_permission_code"),
        schema=SCHEMA,
    )
    op.create_table(
        "role",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("code", name="uq_role_code"),
        schema=SCHEMA,
    )
    op.create_table(
        "role_permission",
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("permission_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["role_id"], [f"{SCHEMA}.role.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["permission_id"], [f"{SCHEMA}.permission.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("role_id", "permission_id"),
        schema=SCHEMA,
    )

    # user_center.role_id
    op.add_column(
        "user_center",
        sa.Column("role_id", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_user_center_role_id",
        "user_center",
        "role",
        ["role_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )

    # ---- 种子数据 ----
    permission_tbl = sa.table(
        "permission",
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        schema=SCHEMA,
    )
    role_tbl = sa.table(
        "role",
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("is_system", sa.Boolean),
        schema=SCHEMA,
    )
    op.bulk_insert(
        permission_tbl,
        [{"code": c, "name": n} for c, n in _PERMISSIONS],
    )
    op.bulk_insert(
        role_tbl,
        [{"code": c, "name": n, "is_system": True} for c, n in _ROLES],
    )

    # 角色-权限映射：按 code 关联写入
    for role_code, perm_codes in _ROLE_PERMS.items():
        codes_sql = ", ".join(f"'{c}'" for c in sorted(perm_codes))
        op.execute(
            f"""
            INSERT INTO {SCHEMA}.role_permission (role_id, permission_id)
            SELECT r.id, p.id
            FROM {SCHEMA}.role r
            JOIN {SCHEMA}.permission p ON p.code IN ({codes_sql})
            WHERE r.code = '{role_code}'
            """
        )

    # ---- 回填既有用户 ----
    op.execute(
        f"""
        UPDATE {SCHEMA}.user_center
        SET role_id = (SELECT id FROM {SCHEMA}.role WHERE code = 'admin')
        WHERE is_super = true AND role_id IS NULL
        """
    )
    op.execute(
        f"""
        UPDATE {SCHEMA}.user_center
        SET role_id = (SELECT id FROM {SCHEMA}.role WHERE code = 'operator')
        WHERE is_super = false AND role_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_constraint("fk_user_center_role_id", "user_center", schema=SCHEMA, type_="foreignkey")
    op.drop_column("user_center", "role_id", schema=SCHEMA)
    op.drop_table("role_permission", schema=SCHEMA)
    op.drop_table("role", schema=SCHEMA)
    op.drop_table("permission", schema=SCHEMA)
