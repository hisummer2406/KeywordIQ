"""定价核算表与权限点

Revision ID: 140193107685
Revises: d7a3b5c9e2f1
Create Date: 2026-11-10

change add-pricing-calculation（Refs #171 #172）D8、D9、D11。

- 新建五张普通表：``pricing_rule``、``pricing_sheet``、``pricing_sheet_version``、
  ``pricing_selection``、``pricing_selection_log``。**不分区**，不登记进
  ``partitioning.PARTITIONED_TABLES``，保留清理不得删除核价历史。
- 不改动任何现有分析表、分区、物化视图或其索引；不写入任何费率数据（费率草稿由
  ``scripts/pricing_seed_drafts.py`` 生成，人工核对后启用）。
- 权限种子：``pricing:view`` 授予 admin/operator/analyst，``pricing:manage-rates`` 只授予
  admin。与 ``app/auth/permissions.py`` 的矩阵保持一致。
- downgrade 删除五张表与两个权限点（含 role_permission 关联）。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from config import settings

revision: str = "140193107685"
down_revision: str | None = "d7a3b5c9e2f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA

_PERMISSIONS = (("pricing:view", "定价核算"), ("pricing:manage-rates", "维护核价费用配置"))
_GRANTS = {
    "pricing:view": ("admin", "operator", "analyst"),
    "pricing:manage-rates": ("admin",),
}


def upgrade() -> None:
    op.create_table('pricing_rule',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False, comment='规则类别：fx_rate/market_params/referral_schedule/fba_rate_card/fbm_channel'),
    sa.Column('scope_key', sa.String(length=64), nullable=False, comment='适用范围：站点码（如 US），汇率为币种（如 USD），自发货渠道为 站点:渠道码'),
    sa.Column('version', sa.Integer(), nullable=False, comment='同一 (kind, scope_key) 内递增'),
    sa.Column('status', sa.String(length=16), server_default='draft', nullable=False, comment='draft/active/retired'),
    sa.Column('effective_from', sa.Date(), nullable=False, comment='生效日（含）'),
    sa.Column('effective_to', sa.Date(), nullable=True, comment='失效日（含），NULL 为长期'),
    sa.Column('source', sa.Text(), nullable=False, comment='来源：官方文档 URL、报价单或原表单元格'),
    sa.Column('review_note', sa.Text(), nullable=True, comment='审核说明，冲突与待核实项'),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='按 kind 校验的规则载荷'),
    sa.Column('payload_schema', sa.Integer(), server_default='1', nullable=False, comment='载荷结构版本'),
    sa.Column('retired_reason', sa.Text(), nullable=True, comment='停用原因'),
    sa.Column('lock_version', sa.Integer(), server_default='1', nullable=False, comment='乐观锁版本号'),
    sa.Column('created_by', sa.Integer(), nullable=True, comment='→ user_center.id'),
    sa.Column('updated_by', sa.Integer(), nullable=True, comment='→ user_center.id'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('retired_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("kind IN ('fx_rate', 'market_params', 'referral_schedule', 'fba_rate_card', 'fbm_channel')", name='ck_pricing_rule_kind'),
    sa.CheckConstraint("status IN ('draft', 'active', 'retired')", name='ck_pricing_rule_status'),
    sa.CheckConstraint('effective_to IS NULL OR effective_to >= effective_from', name='ck_pricing_rule_period'),
    sa.ForeignKeyConstraint(['created_by'], [f'{SCHEMA}.user_center.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['updated_by'], [f'{SCHEMA}.user_center.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('kind', 'scope_key', 'version', name='uq_pricing_rule_version'),
    schema=SCHEMA,
    comment='核价共享费用规则的版本表：草稿可改，启用后不可原地修改，停用保留历史。'
    )
    op.create_index('idx_pricing_rule_lookup', 'pricing_rule', ['kind', 'scope_key', 'status'], unique=False, schema=SCHEMA)
    op.create_table('pricing_sheet',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('owner_id', sa.Integer(), nullable=False, comment='→ user_center.id，唯一归属人；他人访问一律 404'),
    sa.Column('product_name', sa.String(length=200), nullable=False, comment='产品名称'),
    sa.Column('sku', sa.String(length=100), nullable=True),
    sa.Column('asin', sa.String(length=20), nullable=True),
    sa.Column('source_url', sa.Text(), nullable=True, comment='来源链接'),
    sa.Column('status', sa.String(length=16), server_default='draft', nullable=False, comment='draft（未保存过版本）/saved'),
    sa.Column('draft_input', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='当前草稿输入，可反复修改'),
    sa.Column('current_version_id', sa.BigInteger(), nullable=True, comment='最新已保存版本 id'),
    sa.Column('lock_version', sa.Integer(), server_default='1', nullable=False, comment='乐观锁版本号，并发修改草稿返回 409'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('draft', 'saved')", name='ck_pricing_sheet_status'),
    sa.ForeignKeyConstraint(['owner_id'], [f'{SCHEMA}.user_center.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    schema=SCHEMA,
    comment='核价单：创建人私有，草稿可改，保存产生不可变版本。'
    )
    op.create_index('idx_pricing_sheet_owner', 'pricing_sheet', ['owner_id', 'updated_at'], unique=False, schema=SCHEMA)
    op.create_table('pricing_selection_log',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('sheet_id', sa.BigInteger(), nullable=False),
    sa.Column('market', sa.String(length=8), nullable=False),
    sa.Column('version_id', sa.BigInteger(), nullable=False),
    sa.Column('option_key', sa.String(length=80), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('selected_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['selected_by'], [f'{SCHEMA}.user_center.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['sheet_id'], [f'{SCHEMA}.pricing_sheet.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    schema=SCHEMA,
    comment='核价选定方案的留痕：每次选定或换选一行。'
    )
    op.create_index('idx_pricing_selection_log_sheet', 'pricing_selection_log', ['sheet_id', 'market'], unique=False, schema=SCHEMA)
    op.create_table('pricing_sheet_version',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('sheet_id', sa.BigInteger(), nullable=False),
    sa.Column('version_no', sa.Integer(), nullable=False, comment='核价单内从 1 递增'),
    sa.Column('input', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='核价输入快照'),
    sa.Column('rule_refs', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='采用的规则 id/版本/范围/来源与参数副本'),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='服务端计算结果快照'),
    sa.Column('model_version', sa.String(length=16), nullable=False, comment='计算模型版本'),
    sa.Column('calc_date', sa.Date(), nullable=False, comment='核价日期，规则按它命中'),
    sa.Column('client_token', sa.String(length=64), nullable=False, comment='防重复提交的客户端令牌'),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], [f'{SCHEMA}.user_center.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['sheet_id'], [f'{SCHEMA}.pricing_sheet.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('sheet_id', 'client_token', name='uq_pricing_sheet_version_token'),
    sa.UniqueConstraint('sheet_id', 'version_no', name='uq_pricing_sheet_version_no'),
    schema=SCHEMA,
    comment='核价版本快照：保存后不可修改，规则更新不改变历史金额。'
    )
    op.create_table('pricing_selection',
    sa.Column('sheet_id', sa.BigInteger(), nullable=False),
    sa.Column('market', sa.String(length=8), nullable=False, comment='站点码'),
    sa.Column('version_id', sa.BigInteger(), nullable=False),
    sa.Column('option_key', sa.String(length=80), nullable=False, comment='FBA 或 FBM:<渠道码>'),
    sa.Column('note', sa.Text(), nullable=True, comment='选定说明'),
    sa.Column('selected_by', sa.Integer(), nullable=True),
    sa.Column('selected_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['selected_by'], [f'{SCHEMA}.user_center.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['sheet_id'], [f'{SCHEMA}.pricing_sheet.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['version_id'], [f'{SCHEMA}.pricing_sheet_version.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('sheet_id', 'market'),
    schema=SCHEMA,
    comment='核价选定方案：每个市场一条，换选写 pricing_selection_log。'
    )

    # ---- 权限点（与 app/auth/permissions.py 的矩阵一致，D11）----
    for code, name in _PERMISSIONS:
        op.execute(
            sa.text(
                f"INSERT INTO {SCHEMA}.permission (code, name) VALUES (:code, :name) "
                f"ON CONFLICT (code) DO NOTHING"
            ).bindparams(code=code, name=name)
        )
    for code, roles in _GRANTS.items():
        roles_sql = ", ".join(f"'{r}'" for r in roles)
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
    """回滚删除核价表与权限种子；不触碰任何 ABA 事实表、维度表或分区。"""
    for code, _ in _PERMISSIONS:
        op.execute(
            f"""
            DELETE FROM {SCHEMA}.role_permission
             WHERE permission_id IN (SELECT id FROM {SCHEMA}.permission WHERE code = '{code}')
            """
        )
        op.execute(f"DELETE FROM {SCHEMA}.permission WHERE code = '{code}'")
    op.drop_table('pricing_selection', schema=SCHEMA)
    op.drop_table('pricing_sheet_version', schema=SCHEMA)
    op.drop_index('idx_pricing_selection_log_sheet', table_name='pricing_selection_log', schema=SCHEMA)
    op.drop_table('pricing_selection_log', schema=SCHEMA)
    op.drop_index('idx_pricing_sheet_owner', table_name='pricing_sheet', schema=SCHEMA)
    op.drop_table('pricing_sheet', schema=SCHEMA)
    op.drop_index('idx_pricing_rule_lookup', table_name='pricing_rule', schema=SCHEMA)
    op.drop_table('pricing_rule', schema=SCHEMA)
