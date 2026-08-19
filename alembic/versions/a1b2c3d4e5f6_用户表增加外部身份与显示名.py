"""用户表增加外部身份与显示名，口令散列放宽为可空

Revision ID: a1b2c3d4e5f6
Revises: f2a3b4c5d6e7
Create Date: 2026-08-19

钉钉单点登录（变更 dingtalk-sso，issue #36）的数据模型部分。普通表加列，
无分区、无物化视图，autogenerate 能看见，但仍手写以便把下面几条理由留在迁移里。

三列：

- `display_name`：`user_name` 此前同时承担登录名与显示名两个职责。SSO 之后必须拆开——
  钉钉侧的登录标识是英文工号，界面上全是 `zhangsan` 这种字样实际很难用。
- `external_idp` / `external_id`：外部身份来源与该来源内的**稳定**标识。钉钉存 unionId，
  **不存企业内 userid**——后者管理员可改、离职再入职会换，变一次即老账号成孤儿、
  当事人被当作新人重新开号、已配置的角色全部丢失。

一条唯一约束 `(external_idp, external_id)`：账号只按这一对匹配。**绝不按用户名自动绑定
已有账号**——否则任何能修改自己钉钉 userid 的人都可以把它改成 `admin`，扫一次码就接管
超级管理员。PostgreSQL 默认 `NULLS DISTINCT`，本地账号两列均为 NULL 因而互不冲突，
正是所需行为；不要改成 `NULLS NOT DISTINCT`。

`hashed_pwd` 由 NOT NULL 放宽为可空：SSO 账号没有密码，这是**事实**而非哨兵值。塞一个
"永不匹配"的散列，其正确性依赖「bcrypt 不会碰巧匹配」这条隐含约定，NULL 不需要约定。

**降级会删数据。** 收回 `hashed_pwd` 的 NOT NULL 之前，必须先清掉所有没有密码的行，
也就是全部 SSO 账号——它们无法在没有外部身份列的世界里存在（既登不进来，也无法被
重新关联）。`downgrade()` 因此显式删除 `external_idp IS NOT NULL` 的行。这不是疏漏，
是降级的固有代价：回滚这条迁移意味着放弃 SSO，那些账号本就失去意义。真要保住它们，
得先在应用层给每个账号设一个本地密码，再降级。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "analysis"

# 注释文本必须与 app/user/user_model.py 中的 comment= 一字不差。
# 列注释参与 autogenerate 比对，不一致会在输出里留下常驻假差异——历史上约 40 条纯注释
# 差异曾把 user_center 时间戳丢了时区这条真差异整个盖住。
_COMMENTS = {
    "display_name": "展示用名称；SSO 账号取自身份源的姓名",
    "external_idp": "外部身份源；NULL 表示本地账号",
    "external_id": "该身份源内的稳定用户标识",
}


def upgrade() -> None:
    op.add_column(
        "user_center",
        sa.Column("display_name", sa.String(100), nullable=True, comment=_COMMENTS["display_name"]),
        schema=SCHEMA,
    )
    op.add_column(
        "user_center",
        sa.Column("external_idp", sa.String(32), nullable=True, comment=_COMMENTS["external_idp"]),
        schema=SCHEMA,
    )
    op.add_column(
        "user_center",
        sa.Column("external_id", sa.String(128), nullable=True, comment=_COMMENTS["external_id"]),
        schema=SCHEMA,
    )
    op.create_unique_constraint(
        "uq_user_center_external",
        "user_center",
        ["external_idp", "external_id"],
        schema=SCHEMA,
    )
    op.alter_column(
        "user_center",
        "hashed_pwd",
        existing_type=sa.String(255),
        nullable=True,
        schema=SCHEMA,
    )


def downgrade() -> None:
    # 先删掉所有 SSO 账号，否则下面收回 NOT NULL 会失败（它们的 hashed_pwd 是 NULL）。
    # 见模块 docstring：这是降级的固有代价，不是可以绕过的一步。
    op.get_bind().execute(
        sa.text(f"DELETE FROM {SCHEMA}.user_center WHERE external_idp IS NOT NULL")
    )
    op.alter_column(
        "user_center",
        "hashed_pwd",
        existing_type=sa.String(255),
        nullable=False,
        schema=SCHEMA,
    )
    op.drop_constraint("uq_user_center_external", "user_center", schema=SCHEMA, type_="unique")
    op.drop_column("user_center", "external_id", schema=SCHEMA)
    op.drop_column("user_center", "external_idp", schema=SCHEMA)
    op.drop_column("user_center", "display_name", schema=SCHEMA)
