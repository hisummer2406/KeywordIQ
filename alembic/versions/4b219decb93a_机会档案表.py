"""机会档案表与 opportunity:view 授予内置角色

Revision ID: 4b219decb93a
Revises: c4e8a1d2f6b9
Create Date: 2026-11-12

change add-opportunity-dossier（Refs #172、#180）。

- 新建五张普通表：``opp_dossier``、``opp_dossier_keyword``、``opp_dossier_evidence``、
  ``opp_dossier_decision``、``opp_dossier_message``。**不分区**，不登记进
  ``partitioning.PARTITIONED_TABLES``：证据快照与已签署的决定不得被保留清理删除。
- ``opp_ip_research`` 加可空列 ``dossier_id``（``ON DELETE SET NULL``）与部分索引。
- 把 ``opportunity:view`` 授予 admin、operator、analyst（2026-11-12 用户决定推进到运营可用；
  ``533cd79ca75f`` 的 docstring 预告过这一步）。与 ``app/auth/permissions.py`` 的矩阵保持一致。
- 不改动任何事实表、分区或物化视图。
- downgrade 撤销授予、删列、删五张表。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from config import settings

revision: str = "4b219decb93a"
down_revision: str | None = "c4e8a1d2f6b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA

_PERMISSION = "opportunity:view"
_GRANTED_TO = ("admin", "operator", "analyst")
_IP_FK = "opp_ip_research_dossier_id_fkey"


def upgrade() -> None:
    op.create_table('opp_dossier',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('title', sa.String(length=100), nullable=False, comment='标题'),
    sa.Column('idea', sa.Text(), nullable=True, comment='产品想法说明'),
    sa.Column('site', sa.String(length=8), nullable=False, comment='ISO 3166-1 alpha-2 大写站点码（英国用 GB 不用 UK），取值由应用层站点清单约束'),
    sa.Column('creator_id', sa.Integer(), nullable=False, comment='创建人'),
    sa.Column('assignee_id', sa.Integer(), nullable=False, comment='负责人；任何成员可接手'),
    sa.Column('status', sa.String(length=16), server_default=sa.text("'researching'"), nullable=False, comment='最新决定的冗余：researching/advance/need_evidence/watch/reject'),
    sa.Column('recheck_date', sa.Date(), nullable=True, comment='最新决定的复查日期（冗余）'),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True, comment='归档时间；空即未归档'),
    sa.Column('client_token', sa.String(length=64), nullable=False, comment='防重复提交的客户端令牌'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='最近一次写入（证据、决定、关键词、信息）'),
    sa.CheckConstraint("status IN ('researching', 'advance', 'need_evidence', 'watch', 'reject')", name='ck_opp_dossier_status'),
    sa.ForeignKeyConstraint(['assignee_id'], [f'{SCHEMA}.user_center.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['creator_id'], [f'{SCHEMA}.user_center.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('creator_id', 'client_token', name='uq_opp_dossier_creator_token'),
    schema=SCHEMA,
    comment='机会档案（团队可见，不可删除只可归档）'
    )
    op.create_index('idx_opp_dossier_recheck', 'opp_dossier', ['recheck_date'], unique=False, schema=SCHEMA, postgresql_where=sa.text('archived_at IS NULL'))
    op.create_index('idx_opp_dossier_updated', 'opp_dossier', [sa.text('updated_at DESC'), sa.text('id DESC')], unique=False, schema=SCHEMA)
    op.create_table('opp_dossier_evidence',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('dossier_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False, comment='aba_snapshot/manual'),
    sa.Column('anchor_day', sa.Date(), nullable=True, comment='快照时该站点日榜已导入的最新期'),
    sa.Column('anchor_week', sa.Date(), nullable=True, comment='快照时该站点周榜已导入的最新期'),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='快照内容（见 dossier_evidence.py）'),
    sa.Column('category', sa.String(length=16), nullable=True, comment='人工证据的类别'),
    sa.Column('content', sa.Text(), nullable=True, comment='人工证据内容'),
    sa.Column('source_name', sa.String(length=100), nullable=True, comment='来源名称'),
    sa.Column('source_url', sa.String(length=500), nullable=True, comment='来源链接'),
    sa.Column('observed_date', sa.Date(), nullable=True, comment='观测日期'),
    sa.Column('voided_at', sa.DateTime(timezone=True), nullable=True, comment='作废时间'),
    sa.Column('voided_by', sa.Integer(), nullable=True, comment='作废人'),
    sa.Column('created_by', sa.Integer(), nullable=False, comment='生成人 / 填写人'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(kind = 'aba_snapshot' AND payload IS NOT NULL) OR (kind = 'manual' AND category IN ('search_volume', 'sales', 'price', 'bsr', 'reviews', 'supply_cost', 'trademark', 'other') AND content IS NOT NULL AND source_name IS NOT NULL AND observed_date IS NOT NULL)", name='ck_opp_dossier_evidence_shape'),
    sa.CheckConstraint("kind IN ('aba_snapshot', 'manual')", name='ck_opp_dossier_evidence_kind'),
    sa.ForeignKeyConstraint(['created_by'], [f'{SCHEMA}.user_center.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['dossier_id'], [f'{SCHEMA}.opp_dossier.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['voided_by'], [f'{SCHEMA}.user_center.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    schema=SCHEMA,
    comment='机会档案证据：ABA 快照（只追加）与人工证据（只能作废）'
    )
    op.create_index('idx_opp_dossier_evidence_dossier', 'opp_dossier_evidence', ['dossier_id', 'kind', sa.text('id DESC')], unique=False, schema=SCHEMA)
    op.create_table('opp_dossier_keyword',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('dossier_id', sa.BigInteger(), nullable=False),
    sa.Column('keyword', sa.String(length=500), nullable=False, comment='规范化后的关键词（小写、单空格）'),
    sa.Column('keyword_id', sa.BigInteger(), nullable=True, comment='→ dim_keyword（不建外键）；空 = 该站点保留期内未进入过 ABA 榜单'),
    sa.Column('added_by', sa.Integer(), nullable=False, comment='添加人'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['added_by'], [f'{SCHEMA}.user_center.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['dossier_id'], [f'{SCHEMA}.opp_dossier.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dossier_id', 'keyword', name='uq_opp_dossier_keyword'),
    schema=SCHEMA,
    comment='机会档案的关键词'
    )
    op.create_table('opp_dossier_message',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('dossier_id', sa.BigInteger(), nullable=False),
    sa.Column('asker_id', sa.Integer(), nullable=False, comment='提问人'),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=16), server_default=sa.text("'queued'"), nullable=False),
    sa.Column('error_category', sa.String(length=32), nullable=True, comment='失败或未知的分类'),
    sa.Column('answer', sa.Text(), nullable=True),
    sa.Column('cited_evidence_ids', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False, comment='回答引用的本档案证据 id'),
    sa.Column('model', sa.String(length=100), nullable=True),
    sa.Column('usage', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='大模型用量（供应商回报）'),
    sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True, comment='执行租约'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('queued', 'running', 'completed', 'failed', 'unknown')", name='ck_opp_dossier_message_status'),
    sa.ForeignKeyConstraint(['asker_id'], [f'{SCHEMA}.user_center.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['dossier_id'], [f'{SCHEMA}.opp_dossier.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    schema=SCHEMA,
    comment='机会档案追问：问题、回答与队列状态'
    )
    op.create_index('idx_opp_dossier_message_dossier', 'opp_dossier_message', ['dossier_id', 'id'], unique=False, schema=SCHEMA)
    op.create_index('idx_opp_dossier_message_queue', 'opp_dossier_message', ['id'], unique=False, schema=SCHEMA, postgresql_where=sa.text("status = 'queued'"))
    op.create_index('uq_opp_dossier_message_active', 'opp_dossier_message', ['dossier_id'], unique=True, schema=SCHEMA, postgresql_where=sa.text("status IN ('queued', 'running')"))
    op.create_table('opp_dossier_decision',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('dossier_id', sa.BigInteger(), nullable=False),
    sa.Column('verdict', sa.String(length=16), nullable=False, comment='advance/need_evidence/watch/reject'),
    sa.Column('reason', sa.Text(), nullable=False, comment='理由'),
    sa.Column('next_step', sa.Text(), nullable=True, comment='下一步'),
    sa.Column('recheck_date', sa.Date(), nullable=True, comment='复查日期'),
    sa.Column('snapshot_id', sa.BigInteger(), nullable=True, comment='签署时最新的 ABA 快照'),
    sa.Column('ip_status', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='签署时的专利核查状态摘要'),
    sa.Column('signer_id', sa.Integer(), nullable=False, comment='签署人'),
    sa.Column('client_token', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("verdict IN ('advance', 'need_evidence', 'watch', 'reject')", name='ck_opp_dossier_decision_verdict'),
    sa.ForeignKeyConstraint(['dossier_id'], [f'{SCHEMA}.opp_dossier.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['signer_id'], [f'{SCHEMA}.user_center.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['snapshot_id'], [f'{SCHEMA}.opp_dossier_evidence.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dossier_id', 'client_token', name='uq_opp_dossier_decision_token'),
    schema=SCHEMA,
    comment='机会档案的决定（只追加）'
    )
    op.create_index('idx_opp_dossier_decision_dossier', 'opp_dossier_decision', ['dossier_id', 'id'], unique=False, schema=SCHEMA)
    op.add_column('opp_ip_research', sa.Column('dossier_id', sa.BigInteger(), nullable=True, comment='关联的机会档案（change add-opportunity-dossier）；关联后档案向团队展示本调研的摘要，明细仍私有'), schema=SCHEMA)
    op.create_index('idx_opp_ip_research_dossier', 'opp_ip_research', ['dossier_id'], unique=False, schema=SCHEMA, postgresql_where=sa.text('dossier_id IS NOT NULL'))
    op.create_foreign_key(_IP_FK, 'opp_ip_research', 'opp_dossier', ['dossier_id'], ['id'], source_schema=SCHEMA, referent_schema=SCHEMA, ondelete='SET NULL')
    roles_sql = ", ".join(f"'{r}'" for r in _GRANTED_TO)
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.role_permission (role_id, permission_id)
        SELECT r.id, p.id
          FROM {SCHEMA}.role r
          JOIN {SCHEMA}.permission p ON p.code = '{_PERMISSION}'
         WHERE r.code IN ({roles_sql})
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    roles_sql = ", ".join(f"'{r}'" for r in _GRANTED_TO)
    op.execute(
        f"""
        DELETE FROM {SCHEMA}.role_permission
         WHERE permission_id IN (SELECT id FROM {SCHEMA}.permission WHERE code = '{_PERMISSION}')
           AND role_id IN (SELECT id FROM {SCHEMA}.role WHERE code IN ({roles_sql}))
        """
    )
    op.drop_constraint(_IP_FK, 'opp_ip_research', schema=SCHEMA, type_='foreignkey')
    op.drop_index('idx_opp_ip_research_dossier', table_name='opp_ip_research', schema=SCHEMA, postgresql_where=sa.text('dossier_id IS NOT NULL'))
    op.drop_column('opp_ip_research', 'dossier_id', schema=SCHEMA)
    op.drop_index('idx_opp_dossier_decision_dossier', table_name='opp_dossier_decision', schema=SCHEMA)
    op.drop_table('opp_dossier_decision', schema=SCHEMA)
    op.drop_index('uq_opp_dossier_message_active', table_name='opp_dossier_message', schema=SCHEMA, postgresql_where=sa.text("status IN ('queued', 'running')"))
    op.drop_index('idx_opp_dossier_message_queue', table_name='opp_dossier_message', schema=SCHEMA, postgresql_where=sa.text("status = 'queued'"))
    op.drop_index('idx_opp_dossier_message_dossier', table_name='opp_dossier_message', schema=SCHEMA)
    op.drop_table('opp_dossier_message', schema=SCHEMA)
    op.drop_table('opp_dossier_keyword', schema=SCHEMA)
    op.drop_index('idx_opp_dossier_evidence_dossier', table_name='opp_dossier_evidence', schema=SCHEMA)
    op.drop_table('opp_dossier_evidence', schema=SCHEMA)
    op.drop_index('idx_opp_dossier_updated', table_name='opp_dossier', schema=SCHEMA)
    op.drop_index('idx_opp_dossier_recheck', table_name='opp_dossier', schema=SCHEMA, postgresql_where=sa.text('archived_at IS NULL'))
    op.drop_table('opp_dossier', schema=SCHEMA)
