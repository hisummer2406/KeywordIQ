"""机会中心 IP 专家调研表

Revision ID: 3b918ff1ccc9
Revises: b7d3e9f2a6c1
Create Date: 2026-11-11

change add-opportunity-ip-agent（Refs #180）。

- 新建十一张普通表：``opp_workspace``、``opp_ip_research``、``opp_ip_task``、``opp_ip_call``、
  ``opp_ip_query``、``opp_ip_candidate``、``opp_ip_detail``、``opp_ip_agent_output``、``opp_ip_review``、
  ``opp_ip_message``、``opp_ip_schedule``。**不分区**，
  不登记进 ``partitioning.PARTITIONED_TABLES``，保留清理不得删除调研证据与意见。
- 不改动任何现有表、分区、物化视图或权限点（沿用 ``opportunity:view``）。
- downgrade 删除十一张表。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from config import settings

revision: str = "3b918ff1ccc9"
down_revision: str | None = "b7d3e9f2a6c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA


def upgrade() -> None:
    op.create_table('opp_workspace',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('owner_id', sa.Integer(), nullable=False, comment='创建人'),
    sa.Column('name', sa.String(length=50), nullable=False, comment='名称（同一用户下不重复）'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], [f'{SCHEMA}.user_center.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('owner_id', 'name', name='uq_opp_workspace_owner_name'),
    schema=SCHEMA,
    comment='机会中心工作空间（按创建人私有）'
    )
    op.create_table('opp_ip_research',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('owner_id', sa.Integer(), nullable=False, comment='创建人；记录按创建人私有，他人（含超管）访问一律 404'),
    sa.Column('product_name', sa.String(length=200), nullable=False, comment='产品名称'),
    sa.Column('description', sa.Text(), nullable=True, comment='产品说明、卖点、外观特征'),
    sa.Column('keywords', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='关键词（英文为主）'),
    sa.Column('loc_classes', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='洛迦诺分类，如 07-01'),
    sa.Column('jurisdictions', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='检索法域编码'),
    sa.Column('markets', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='销售市场站点码（人工意见按它签）'),
    sa.Column('workspace_id', sa.BigInteger(), nullable=True, comment='所属工作空间；空即未归类（可移动，不属于调研输入）'),
    sa.Column('client_token', sa.String(length=64), nullable=False, comment='防重复提交的客户端令牌'),
    sa.Column('input_fingerprint', sa.String(length=64), nullable=False, comment='规范化输入指纹'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], [f'{SCHEMA}.user_center.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['workspace_id'], [f'{SCHEMA}.opp_workspace.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('owner_id', 'client_token', name='uq_opp_ip_research_owner_token'),
    schema=SCHEMA,
    comment='机会中心专利调研：产品信息、检索法域、销售市场'
    )
    op.create_index('idx_opp_ip_research_owner_created', 'opp_ip_research', ['owner_id', sa.text('created_at DESC'), sa.text('id DESC')], unique=False, schema=SCHEMA)
    op.create_index('idx_opp_ip_research_workspace', 'opp_ip_research', ['workspace_id'], unique=False, schema=SCHEMA)
    op.create_table('opp_ip_review',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('research_id', sa.BigInteger(), nullable=False),
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
    sa.CheckConstraint("decision IN ('pass', 'conditional_pass', 'continue_verify', 'need_evidence', 'not_recommended')", name='ck_opp_ip_review_decision'),
    sa.CheckConstraint("risk_level IN ('high', 'medium', 'low', 'undetermined')", name='ck_opp_ip_review_risk_level'),
    sa.ForeignKeyConstraint(['research_id'], [f'{SCHEMA}.opp_ip_research.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['signer_id'], [f'{SCHEMA}.user_center.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('research_id', 'client_token', name='uq_opp_ip_review_research_token'),
    schema=SCHEMA,
    comment='IP 专家调研的人工意见（按市场，只追加）'
    )
    op.create_index('idx_opp_ip_review_research_market', 'opp_ip_review', ['research_id', 'market', 'id'], unique=False, schema=SCHEMA)
    op.create_table('opp_ip_task',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('research_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False, comment='plan/search/detail/analyze/ask'),
    sa.Column('status', sa.String(length=16), server_default='queued', nullable=False, comment='queued/running/completed/failed/unknown'),
    sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='固定的任务参数'),
    sa.Column('planned_mcp_calls', sa.Integer(), server_default='0', nullable=False, comment='入队时核对预算用的计划 MCP 调用数'),
    sa.Column('client_token', sa.String(length=64), nullable=False),
    sa.Column('input_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('error_category', sa.String(length=32), nullable=True, comment='失败或未知的分类'),
    sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True, comment='运行租约到期时间'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("kind IN ('plan', 'search', 'detail', 'analyze', 'ask')", name='ck_opp_ip_task_kind'),
    sa.CheckConstraint("status IN ('queued', 'running', 'completed', 'failed', 'unknown')", name='ck_opp_ip_task_status'),
    sa.ForeignKeyConstraint(['research_id'], [f'{SCHEMA}.opp_ip_research.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('research_id', 'client_token', name='uq_opp_ip_task_research_token'),
    schema=SCHEMA,
    comment='IP 专家任务：持久化队列，SKIP LOCKED 认领 + 租约'
    )
    op.create_index('idx_opp_ip_task_queued', 'opp_ip_task', ['created_at', 'id'], unique=False, schema=SCHEMA, postgresql_where=sa.text("status = 'queued'"))
    op.create_index('idx_opp_ip_task_research', 'opp_ip_task', ['research_id', 'id'], unique=False, schema=SCHEMA)
    op.create_index('uq_opp_ip_task_active', 'opp_ip_task', ['research_id'], unique=True, schema=SCHEMA, postgresql_where=sa.text("status IN ('queued', 'running')"))
    op.create_table('opp_ip_message',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('research_id', sa.BigInteger(), nullable=False),
    sa.Column('task_id', sa.BigInteger(), nullable=False, comment='用户消息对应它触发的 ask 任务；Agent 回答对应生成它的任务'),
    sa.Column('role', sa.String(length=8), nullable=False, comment='user/agent'),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('model', sa.String(length=100), nullable=True, comment='Agent 回答所用模型'),
    sa.Column('cited_candidate_ids', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False, comment='回答引用的本调研候选 id'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("role IN ('user', 'agent')", name='ck_opp_ip_message_role'),
    sa.ForeignKeyConstraint(['research_id'], [f'{SCHEMA}.opp_ip_research.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], [f'{SCHEMA}.opp_ip_task.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    schema=SCHEMA,
    comment='IP 专家任务对话：用户追问与 Agent 回答'
    )
    op.create_index('idx_opp_ip_message_research', 'opp_ip_message', ['research_id', 'id'], unique=False, schema=SCHEMA)
    op.create_table('opp_ip_schedule',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('research_id', sa.BigInteger(), nullable=False),
    sa.Column('frequency', sa.String(length=16), nullable=False, comment='weekly/monthly'),
    sa.Column('weekday', sa.Integer(), nullable=True, comment='weekly：0=周一 … 6=周日'),
    sa.Column('day_of_month', sa.Integer(), nullable=True, comment='monthly：1–28'),
    sa.Column('hour', sa.Integer(), nullable=False, comment='执行时刻（Asia/Shanghai，0–23 点）'),
    sa.Column('queries', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='复检的检索式 [{query_text, limit}]'),
    sa.Column('enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=False, comment='下次执行时间'),
    sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True, comment='最近一次到期处理时间'),
    sa.Column('last_status', sa.String(length=16), nullable=True, comment='queued/skipped'),
    sa.Column('last_reason', sa.Text(), nullable=True, comment='跳过原因'),
    sa.Column('last_task_id', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("frequency IN ('weekly', 'monthly')", name='ck_opp_ip_schedule_frequency'),
    sa.CheckConstraint('hour BETWEEN 0 AND 23', name='ck_opp_ip_schedule_hour'),
    sa.CheckConstraint("(frequency = 'weekly' AND weekday BETWEEN 0 AND 6 AND day_of_month IS NULL) OR (frequency = 'monthly' AND day_of_month BETWEEN 1 AND 28 AND weekday IS NULL)", name='ck_opp_ip_schedule_when'),
    sa.CheckConstraint("last_status IS NULL OR last_status IN ('queued', 'skipped')", name='ck_opp_ip_schedule_last_status'),
    sa.ForeignKeyConstraint(['last_task_id'], [f'{SCHEMA}.opp_ip_task.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['research_id'], [f'{SCHEMA}.opp_ip_research.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('research_id', name='uq_opp_ip_schedule_research'),
    schema=SCHEMA,
    comment='IP 专家定时复检'
    )
    op.create_index('idx_opp_ip_schedule_due', 'opp_ip_schedule', ['next_run_at'], unique=False, schema=SCHEMA, postgresql_where=sa.text('enabled'))
    op.create_table('opp_ip_agent_output',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('research_id', sa.BigInteger(), nullable=False),
    sa.Column('task_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False, comment='plan/analysis'),
    sa.Column('model', sa.String(length=100), nullable=False, comment='生成所用模型'),
    sa.Column('content', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='校验后的结构化输出'),
    sa.Column('coverage', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='分析时服务端算出的覆盖事实（不由模型生成）'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('plan', 'analysis')", name='ck_opp_ip_agent_output_kind'),
    sa.ForeignKeyConstraint(['research_id'], [f'{SCHEMA}.opp_ip_research.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], [f'{SCHEMA}.opp_ip_task.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    schema=SCHEMA,
    comment='IP 专家 Agent 输出（检索方案、风险分析）'
    )
    op.create_index('idx_opp_ip_agent_output_research', 'opp_ip_agent_output', ['research_id', 'kind', 'id'], unique=False, schema=SCHEMA)
    op.create_table('opp_ip_call',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('research_id', sa.BigInteger(), nullable=False),
    sa.Column('task_id', sa.BigInteger(), nullable=False),
    sa.Column('provider', sa.String(length=8), nullable=False, comment='mcp/llm'),
    sa.Column('tool', sa.String(length=64), nullable=False, comment='MCP 工具名，或 llm 的 plan/analysis'),
    sa.Column('args', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='参数摘要（不含 Key 与提示词全文）'),
    sa.Column('outcome', sa.String(length=16), nullable=False, comment='success/error/unknown'),
    sa.Column('http_status', sa.Integer(), nullable=True),
    sa.Column('error_code', sa.BigInteger(), nullable=True, comment='供应商 error_code'),
    sa.Column('error_category', sa.String(length=32), nullable=True),
    sa.Column('billing', sa.String(length=16), server_default='unknown', nullable=False, comment='计费状态：无可靠回执，恒为 unknown'),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("outcome IN ('success', 'error', 'unknown')", name='ck_opp_ip_call_outcome'),
    sa.CheckConstraint("provider IN ('mcp', 'llm')", name='ck_opp_ip_call_provider'),
    sa.ForeignKeyConstraint(['research_id'], [f'{SCHEMA}.opp_ip_research.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], [f'{SCHEMA}.opp_ip_task.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    schema=SCHEMA,
    comment='IP 专家外部调用留痕（MCP 预算按它计数）'
    )
    op.create_index('idx_opp_ip_call_provider_started', 'opp_ip_call', ['provider', 'started_at'], unique=False, schema=SCHEMA)
    op.create_index('idx_opp_ip_call_research', 'opp_ip_call', ['research_id', 'provider'], unique=False, schema=SCHEMA)
    op.create_table('opp_ip_query',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('research_id', sa.BigInteger(), nullable=False),
    sa.Column('task_id', sa.BigInteger(), nullable=False),
    sa.Column('query_text', sa.Text(), nullable=False, comment='人工确认的检索式'),
    sa.Column('limit', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False, comment='success/failed/unknown'),
    sa.Column('error_category', sa.String(length=32), nullable=True),
    sa.Column('total_count', sa.Integer(), nullable=True, comment='来源匹配总数'),
    sa.Column('returned_count', sa.Integer(), nullable=True, comment='本次实际返回条数'),
    sa.Column('raw', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='供应商原始 data'),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('success', 'failed', 'unknown')", name='ck_opp_ip_query_status'),
    sa.ForeignKeyConstraint(['research_id'], [f'{SCHEMA}.opp_ip_research.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], [f'{SCHEMA}.opp_ip_task.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    schema=SCHEMA,
    comment='IP 专家检索式与结果'
    )
    op.create_index('idx_opp_ip_query_research', 'opp_ip_query', ['research_id', 'id'], unique=False, schema=SCHEMA)
    op.create_table('opp_ip_candidate',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('research_id', sa.BigInteger(), nullable=False),
    sa.Column('query_id', sa.BigInteger(), nullable=False, comment='首次出现的检索式'),
    sa.Column('rank', sa.Integer(), nullable=False, comment='首次出现时在该检索式结果中的位置（从 1 起）'),
    sa.Column('patent_id', sa.String(length=64), nullable=False),
    sa.Column('pn', sa.String(length=64), nullable=True, comment='公开（公告）号'),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('authority', sa.String(length=16), nullable=True, comment='受理局'),
    sa.Column('apno', sa.String(length=64), nullable=True),
    sa.Column('apdt', sa.Integer(), nullable=True, comment='申请日 yyyyMMdd'),
    sa.Column('pbdt', sa.Integer(), nullable=True, comment='公开日 yyyyMMdd'),
    sa.Column('assignee', sa.Text(), nullable=True, comment='当前权利人'),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['query_id'], [f'{SCHEMA}.opp_ip_query.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['research_id'], [f'{SCHEMA}.opp_ip_research.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('research_id', 'patent_id', name='uq_opp_ip_candidate_research_patent'),
    schema=SCHEMA,
    comment='IP 专家候选专利'
    )
    op.create_index('idx_opp_ip_candidate_research', 'opp_ip_candidate', ['research_id', 'id'], unique=False, schema=SCHEMA)
    op.create_table('opp_ip_detail',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('candidate_id', sa.BigInteger(), nullable=False),
    sa.Column('tool', sa.String(length=32), nullable=False, comment='get_patent_legal_status/bibliography/abstract_image'),
    sa.Column('status', sa.String(length=16), nullable=False, comment='success/failed/unknown/missing'),
    sa.Column('task_id', sa.BigInteger(), nullable=False),
    sa.Column('error_category', sa.String(length=32), nullable=True),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='规范化字段 + 原始条目'),
    sa.Column('image', sa.LargeBinary(), nullable=True, comment='摘要附图本体（取证时下载；供应商地址约 1 小时失效）'),
    sa.Column('image_content_type', sa.String(length=32), nullable=True),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('success', 'failed', 'unknown', 'missing')", name='ck_opp_ip_detail_status'),
    sa.CheckConstraint("tool IN ('get_patent_legal_status', 'bibliography', 'abstract_image')", name='ck_opp_ip_detail_tool'),
    sa.ForeignKeyConstraint(['candidate_id'], [f'{SCHEMA}.opp_ip_candidate.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], [f'{SCHEMA}.opp_ip_task.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('candidate_id', 'tool', name='uq_opp_ip_detail_candidate_tool'),
    schema=SCHEMA,
    comment='IP 专家候选详情与摘要附图'
    )


def downgrade() -> None:
    op.drop_index('idx_opp_ip_schedule_due', table_name='opp_ip_schedule', schema=SCHEMA, postgresql_where=sa.text('enabled'))
    op.drop_table('opp_ip_schedule', schema=SCHEMA)
    op.drop_index('idx_opp_ip_message_research', table_name='opp_ip_message', schema=SCHEMA)
    op.drop_table('opp_ip_message', schema=SCHEMA)
    op.drop_table('opp_ip_detail', schema=SCHEMA)
    op.drop_index('idx_opp_ip_candidate_research', table_name='opp_ip_candidate', schema=SCHEMA)
    op.drop_table('opp_ip_candidate', schema=SCHEMA)
    op.drop_index('idx_opp_ip_query_research', table_name='opp_ip_query', schema=SCHEMA)
    op.drop_table('opp_ip_query', schema=SCHEMA)
    op.drop_index('idx_opp_ip_call_research', table_name='opp_ip_call', schema=SCHEMA)
    op.drop_index('idx_opp_ip_call_provider_started', table_name='opp_ip_call', schema=SCHEMA)
    op.drop_table('opp_ip_call', schema=SCHEMA)
    op.drop_index('idx_opp_ip_agent_output_research', table_name='opp_ip_agent_output', schema=SCHEMA)
    op.drop_table('opp_ip_agent_output', schema=SCHEMA)
    op.drop_index('uq_opp_ip_task_active', table_name='opp_ip_task', schema=SCHEMA, postgresql_where=sa.text("status IN ('queued', 'running')"))
    op.drop_index('idx_opp_ip_task_research', table_name='opp_ip_task', schema=SCHEMA)
    op.drop_index('idx_opp_ip_task_queued', table_name='opp_ip_task', schema=SCHEMA, postgresql_where=sa.text("status = 'queued'"))
    op.drop_table('opp_ip_task', schema=SCHEMA)
    op.drop_index('idx_opp_ip_review_research_market', table_name='opp_ip_review', schema=SCHEMA)
    op.drop_table('opp_ip_review', schema=SCHEMA)
    op.drop_index('idx_opp_ip_research_workspace', table_name='opp_ip_research', schema=SCHEMA)
    op.drop_index('idx_opp_ip_research_owner_created', table_name='opp_ip_research', schema=SCHEMA)
    op.drop_table('opp_ip_research', schema=SCHEMA)
    op.drop_table('opp_workspace', schema=SCHEMA)
