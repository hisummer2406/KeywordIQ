"""外观专利核查表与权限点

Revision ID: b8b59f9e172f
Revises: e8b2c6d4f1a3
Create Date: 2026-11-10

change add-patent-risk-check（Refs #24）。

- 新建八张普通表：``risk_check``、``risk_check_image``、``risk_task``、``risk_provider_call``、
  ``risk_search_page``、``risk_candidate``、``risk_patent_detail``、``risk_review``。**不分区**，
  不登记进 ``partitioning.PARTITIONED_TABLES``，保留清理不得删除核查证据与意见。
- 不改动任何现有分析表、分区、物化视图或其索引。
- 权限种子：``risk:view``、``risk:search``、``risk:review`` 只授予 admin，与
  ``app/auth/permissions.py`` 的矩阵一致（design 决策 9）。
- downgrade 删除八张表与三个权限点（含 role_permission 关联）。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from config import settings

revision: str = "b8b59f9e172f"
down_revision: str | None = "e8b2c6d4f1a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA

_PERMISSIONS = (
    ("risk:view", "查看风险核查"),
    ("risk:search", "发起专利检索（计费）"),
    ("risk:review", "提交风险审核意见"),
)
_GRANT_ROLES = ("admin",)


def upgrade() -> None:
    op.create_table('risk_check',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('owner_id', sa.Integer(), nullable=False, comment='创建人；记录按创建人私有，他人（含超管）访问一律 404'),
    sa.Column('product_name', sa.String(length=200), nullable=False, comment='产品名称'),
    sa.Column('product_version', sa.String(length=100), nullable=False, comment='产品版本'),
    sa.Column('source_ref', sa.String(length=500), nullable=True, comment='来源链接或 ASIN 备注'),
    sa.Column('description', sa.Text(), nullable=True, comment='产品说明'),
    sa.Column('markets', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='销售市场站点码列表'),
    sa.Column('jurisdictions', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='用户明确勾选的检索法域编码列表（与销售市场相互独立）'),
    sa.Column('page_size', sa.Integer(), nullable=False, comment='每页读取条数：10/20/50'),
    sa.Column('previous_check_id', sa.BigInteger(), nullable=True, comment='上一轮核查（同一创建人）'),
    sa.Column('client_token', sa.String(length=64), nullable=False, comment='防重复提交的客户端令牌'),
    sa.Column('input_fingerprint', sa.String(length=64), nullable=False, comment='规范化输入 + 图片 sha256 的指纹；同令牌不同指纹 → 409'),
    sa.Column('search_status', sa.String(length=16), server_default='queued', nullable=False, comment='最近一个检索任务的状态：queued/running/completed/failed/unknown'),
    sa.Column('fetched_count', sa.Integer(), server_default='0', nullable=False, comment='已读取到的来源结果位置（下一页 offset）'),
    sa.Column('total_count', sa.Integer(), nullable=True, comment='供应商最近一次返回的来源匹配总数'),
    sa.Column('upload_url', sa.Text(), nullable=True, comment='P010 返回的临时图片地址（约 9 小时过期）'),
    sa.Column('upload_expires_at', sa.DateTime(timezone=True), nullable=True, comment='临时地址过期时间'),
    sa.Column('searched_at', sa.DateTime(timezone=True), nullable=True, comment='最近一次检索完成时间'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("search_status IN ('queued', 'running', 'completed', 'failed', 'unknown')", name='ck_risk_check_search_status'),
    sa.ForeignKeyConstraint(['owner_id'], [f'{SCHEMA}.user_center.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['previous_check_id'], [f'{SCHEMA}.risk_check.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('owner_id', 'client_token', name='uq_risk_check_owner_token'),
    schema=SCHEMA,
    comment='外观专利核查单：产品版本快照、销售市场、检索法域'
    )
    op.create_index('idx_risk_check_owner_created', 'risk_check', ['owner_id', sa.text('created_at DESC'), sa.text('id DESC')], unique=False, schema=SCHEMA)
    op.create_table('risk_check_image',
    sa.Column('check_id', sa.BigInteger(), nullable=False),
    sa.Column('content_type', sa.String(length=32), nullable=False, comment='image/jpeg 或 image/png'),
    sa.Column('size_bytes', sa.Integer(), nullable=False, comment='字节数'),
    sa.Column('sha256', sa.String(length=64), nullable=False, comment='内容 sha256'),
    sa.Column('data', sa.LargeBinary(), nullable=False, comment='原图内容'),
    sa.ForeignKeyConstraint(['check_id'], [f'{SCHEMA}.risk_check.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('check_id'),
    schema=SCHEMA,
    comment='核查原图（只经鉴权端点读取）'
    )
    op.create_table('risk_review',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('check_id', sa.BigInteger(), nullable=False),
    sa.Column('market', sa.String(length=8), nullable=False, comment='销售市场站点码'),
    sa.Column('risk_level', sa.String(length=16), nullable=False, comment='high/medium/low/undetermined'),
    sa.Column('decision', sa.String(length=24), nullable=False, comment='pass/conditional_pass/continue_verify/need_evidence/not_recommended'),
    sa.Column('basis', sa.Text(), nullable=False, comment='依据'),
    sa.Column('uncovered_scope', sa.Text(), nullable=False, comment='未覆盖范围'),
    sa.Column('conditions', sa.Text(), nullable=True, comment='整改条件'),
    sa.Column('evidence_candidate_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='引用的候选 id'),
    sa.Column('review_date', sa.Date(), nullable=True, comment='复核日期'),
    sa.Column('signer_id', sa.Integer(), nullable=False),
    sa.Column('client_token', sa.String(length=64), nullable=False),
    sa.Column('input_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("decision IN ('pass', 'conditional_pass', 'continue_verify', 'need_evidence', 'not_recommended')", name='ck_risk_review_decision'),
    sa.CheckConstraint("risk_level IN ('high', 'medium', 'low', 'undetermined')", name='ck_risk_review_risk_level'),
    sa.ForeignKeyConstraint(['check_id'], [f'{SCHEMA}.risk_check.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['signer_id'], [f'{SCHEMA}.user_center.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('check_id', 'client_token', name='uq_risk_review_check_token'),
    schema=SCHEMA,
    comment='按市场的人工意见（只追加）'
    )
    op.create_index('idx_risk_review_check_market', 'risk_review', ['check_id', 'market', 'id'], unique=False, schema=SCHEMA)
    op.create_table('risk_task',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('check_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False, comment='search（检索一页）/detail（著录与法律状态）'),
    sa.Column('status', sa.String(length=16), server_default='queued', nullable=False, comment='queued/running/completed/failed/unknown'),
    sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='固定的任务参数'),
    sa.Column('client_token', sa.String(length=64), nullable=False),
    sa.Column('input_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('error_category', sa.String(length=32), nullable=True, comment='失败或未知的分类'),
    sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True, comment='运行租约到期时间'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("kind IN ('search', 'detail')", name='ck_risk_task_kind'),
    sa.CheckConstraint("status IN ('queued', 'running', 'completed', 'failed', 'unknown')", name='ck_risk_task_status'),
    sa.ForeignKeyConstraint(['check_id'], [f'{SCHEMA}.risk_check.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('check_id', 'client_token', name='uq_risk_task_check_token'),
    schema=SCHEMA,
    comment='核查任务：持久化队列，SKIP LOCKED 认领 + 租约'
    )
    op.create_index('idx_risk_task_check', 'risk_task', ['check_id', 'id'], unique=False, schema=SCHEMA)
    op.create_index('idx_risk_task_queued', 'risk_task', ['created_at', 'id'], unique=False, schema=SCHEMA, postgresql_where=sa.text("status = 'queued'"))
    op.create_index('uq_risk_task_active', 'risk_task', ['check_id', 'kind'], unique=True, schema=SCHEMA, postgresql_where=sa.text("status IN ('queued', 'running')"))
    op.create_table('risk_provider_call',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('task_id', sa.BigInteger(), nullable=False),
    sa.Column('api', sa.String(length=8), nullable=False, comment='P010/P060/P011/P013'),
    sa.Column('outcome', sa.String(length=16), nullable=False, comment='success/error/unknown'),
    sa.Column('http_status', sa.Integer(), nullable=True),
    sa.Column('error_code', sa.BigInteger(), nullable=True, comment='供应商 error_code'),
    sa.Column('error_category', sa.String(length=32), nullable=True),
    sa.Column('billing', sa.String(length=16), server_default='unknown', nullable=False, comment='计费状态：无可靠回执，恒为 unknown'),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("outcome IN ('success', 'error', 'unknown')", name='ck_risk_provider_call_outcome'),
    sa.ForeignKeyConstraint(['task_id'], [f'{SCHEMA}.risk_task.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    schema=SCHEMA,
    comment='智慧芽调用留痕'
    )
    op.create_index('idx_risk_provider_call_task', 'risk_provider_call', ['task_id'], unique=False, schema=SCHEMA)
    op.create_table('risk_search_page',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('check_id', sa.BigInteger(), nullable=False),
    sa.Column('task_id', sa.BigInteger(), nullable=False),
    sa.Column('offset', sa.Integer(), nullable=False),
    sa.Column('limit', sa.Integer(), nullable=False),
    sa.Column('returned_count', sa.Integer(), nullable=False, comment='本页实际返回条数'),
    sa.Column('total_count', sa.Integer(), nullable=False, comment='来源匹配总数'),
    sa.Column('request_params', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='不含图片地址的请求参数'),
    sa.Column('raw', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='供应商原始 data'),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['check_id'], [f'{SCHEMA}.risk_check.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], [f'{SCHEMA}.risk_task.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    schema=SCHEMA,
    comment='检索分页证据'
    )
    op.create_index('idx_risk_search_page_check', 'risk_search_page', ['check_id', 'offset'], unique=False, schema=SCHEMA)
    op.create_table('risk_candidate',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('check_id', sa.BigInteger(), nullable=False),
    sa.Column('page_id', sa.BigInteger(), nullable=False, comment='首次出现的页'),
    sa.Column('rank', sa.Integer(), nullable=False, comment='首次出现时在来源结果中的位置（从 1 起）'),
    sa.Column('patent_id', sa.String(length=64), nullable=False),
    sa.Column('patent_pn', sa.String(length=64), nullable=True, comment='公开（公告）号'),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('image_url', sa.Text(), nullable=True, comment='供应商附图地址（会过期，只记来源与取证时间）'),
    sa.Column('score', sa.Float(), nullable=True, comment='来源相似度，只用于排序'),
    sa.Column('authority', sa.String(length=16), nullable=True, comment='受理局'),
    sa.Column('apno', sa.String(length=64), nullable=True),
    sa.Column('apdt', sa.Integer(), nullable=True, comment='申请日 yyyyMMdd'),
    sa.Column('pbdt', sa.Integer(), nullable=True, comment='公开日 yyyyMMdd'),
    sa.Column('assignee', sa.Text(), nullable=True, comment='当前权利人'),
    sa.Column('loc', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='洛迦诺分类'),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['check_id'], [f'{SCHEMA}.risk_check.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['page_id'], [f'{SCHEMA}.risk_search_page.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('check_id', 'patent_id', name='uq_risk_candidate_check_patent'),
    schema=SCHEMA,
    comment='候选外观专利'
    )
    op.create_index('idx_risk_candidate_check_rank', 'risk_candidate', ['check_id', 'rank'], unique=False, schema=SCHEMA)
    op.create_table('risk_patent_detail',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('candidate_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False, comment='bibliography/legal'),
    sa.Column('status', sa.String(length=16), nullable=False, comment='success/failed/unknown/missing'),
    sa.Column('task_id', sa.BigInteger(), nullable=False),
    sa.Column('error_category', sa.String(length=32), nullable=True),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='规范化字段 + 原始条目'),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("kind IN ('bibliography', 'legal')", name='ck_risk_patent_detail_kind'),
    sa.CheckConstraint("status IN ('success', 'failed', 'unknown', 'missing')", name='ck_risk_patent_detail_status'),
    sa.ForeignKeyConstraint(['candidate_id'], [f'{SCHEMA}.risk_candidate.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], [f'{SCHEMA}.risk_task.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('candidate_id', 'kind', name='uq_risk_patent_detail_candidate_kind'),
    schema=SCHEMA,
    comment='候选专利的著录与法律状态'
    )

    # ---- 权限点（与 app/auth/permissions.py 的矩阵一致，决策 9）----
    for code, name in _PERMISSIONS:
        op.execute(
            sa.text(
                f"INSERT INTO {SCHEMA}.permission (code, name) VALUES (:code, :name) "
                f"ON CONFLICT (code) DO NOTHING"
            ).bindparams(code=code, name=name)
        )
        roles_sql = ", ".join(f"'{r}'" for r in _GRANT_ROLES)
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
    """回滚删除核查表与权限种子；不触碰任何 ABA 事实表、维度表或分区。"""
    for code, _ in _PERMISSIONS:
        op.execute(
            f"""
            DELETE FROM {SCHEMA}.role_permission
             WHERE permission_id IN (SELECT id FROM {SCHEMA}.permission WHERE code = '{code}')
            """
        )
        op.execute(f"DELETE FROM {SCHEMA}.permission WHERE code = '{code}'")
    op.drop_table('risk_patent_detail', schema=SCHEMA)
    op.drop_index('idx_risk_candidate_check_rank', table_name='risk_candidate', schema=SCHEMA)
    op.drop_table('risk_candidate', schema=SCHEMA)
    op.drop_index('idx_risk_search_page_check', table_name='risk_search_page', schema=SCHEMA)
    op.drop_table('risk_search_page', schema=SCHEMA)
    op.drop_index('idx_risk_provider_call_task', table_name='risk_provider_call', schema=SCHEMA)
    op.drop_table('risk_provider_call', schema=SCHEMA)
    op.drop_index('uq_risk_task_active', table_name='risk_task', schema=SCHEMA, postgresql_where=sa.text("status IN ('queued', 'running')"))
    op.drop_index('idx_risk_task_queued', table_name='risk_task', schema=SCHEMA, postgresql_where=sa.text("status = 'queued'"))
    op.drop_index('idx_risk_task_check', table_name='risk_task', schema=SCHEMA)
    op.drop_table('risk_task', schema=SCHEMA)
    op.drop_index('idx_risk_review_check_market', table_name='risk_review', schema=SCHEMA)
    op.drop_table('risk_review', schema=SCHEMA)
    op.drop_table('risk_check_image', schema=SCHEMA)
    op.drop_index('idx_risk_check_owner_created', table_name='risk_check', schema=SCHEMA)
    op.drop_table('risk_check', schema=SCHEMA)
