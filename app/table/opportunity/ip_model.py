"""机会中心 IP 专家 Agent 的持久化模型（change add-opportunity-ip-agent）。

十一张普通表（``opp_workspace`` + ``opp_ip_*`` 十张），**不分区、不登记进 partitioning.PARTITIONED_TABLES**：调研证据与人工意见是选品
决策的依据，按月 DROP 会让已签署的意见失去出处。不与 ABA 事实表关联。

- ``opp_ip_research``：调研记录，输入创建后不可改。
- ``opp_ip_task``：持久化任务（plan/search/detail/analyze），``SKIP LOCKED`` 认领 + 租约。
- ``opp_ip_call``：每一次 MCP / 大模型调用的留痕，计费状态恒为未知；MCP 预算按它计数。
- ``opp_ip_query``：每条执行过的检索式及其结果。
- ``opp_ip_candidate`` / ``opp_ip_detail``：去重候选与按需取证（含摘要附图本体）。
- ``opp_ip_agent_output``：Agent 生成的检索方案与风险分析，与人工意见分开。
- ``opp_ip_review``：按市场追加的人工意见（规则与 #24 共用 ``risk/review_rules.py``）。
- ``opp_workspace``：工作空间，组织任务用（决策 11）。
- ``opp_ip_message``：任务对话里的追问与 Agent 回答（决策 12）。
- ``opp_ip_schedule``：定时复检（决策 13）。

私有性靠每条查询带 ``opp_ip_research.owner_id``，取不到一律 404。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.table.risk.risk_model import DECISIONS, RISK_LEVELS
from app.user import user_model  # noqa: F401 - 外键目标 user_center 须先注册进同一 metadata
from config import settings
from database import Base

_SCHEMA = settings.DATABASE_SCHEMA

TASK_KINDS = ("plan", "search", "detail", "analyze", "ask")
TASK_STATUSES = ("queued", "running", "completed", "failed", "unknown")
ACTIVE_TASK_STATUSES = ("queued", "running")
PROVIDERS = ("mcp", "llm")
CALL_OUTCOMES = ("success", "error", "unknown")
QUERY_STATUSES = ("success", "failed", "unknown")
DETAIL_TOOLS = ("get_patent_legal_status", "bibliography", "abstract_image")
DETAIL_STATUSES = ("success", "failed", "unknown", "missing")
OUTPUT_KINDS = ("plan", "analysis")
MESSAGE_ROLES = ("user", "agent")
SCHEDULE_FREQUENCIES = ("weekly", "monthly")
SCHEDULE_RUN_STATUSES = ("queued", "skipped")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


def _fk(table: str, ondelete: str = "CASCADE") -> ForeignKey:
    return ForeignKey(f"{_SCHEMA}.{table}.id", ondelete=ondelete)


class OppWorkspace(Base):
    """工作空间：按创建人私有的任务分组。"""

    __tablename__ = "opp_workspace"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, _fk("user_center", "RESTRICT"), nullable=False, comment="创建人")
    name: Mapped[str] = mapped_column(String(50), nullable=False, comment="名称（同一用户下不重复）")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_opp_workspace_owner_name"),
        {"schema": _SCHEMA, "comment": "机会中心工作空间（按创建人私有）"},
    )


class OppIpResearch(Base):
    """一次专利调研。输入快照创建后不可修改。"""

    __tablename__ = "opp_ip_research"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, _fk("user_center", "RESTRICT"), nullable=False,
        comment="创建人；记录按创建人私有，他人（含超管）访问一律 404",
    )
    product_name: Mapped[str] = mapped_column(String(200), nullable=False, comment="产品名称")
    description: Mapped[str | None] = mapped_column(Text, comment="产品说明、卖点、外观特征")
    keywords: Mapped[list[str]] = mapped_column(JSONB, nullable=False, comment="关键词（英文为主）")
    loc_classes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, comment="洛迦诺分类，如 07-01")
    jurisdictions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, comment="检索法域编码")
    markets: Mapped[list[str]] = mapped_column(JSONB, nullable=False, comment="销售市场站点码（人工意见按它签）")
    workspace_id: Mapped[int | None] = mapped_column(
        BigInteger, _fk("opp_workspace", "SET NULL"), comment="所属工作空间；空即未归类（可移动，不属于调研输入）",
    )
    client_token: Mapped[str] = mapped_column(String(64), nullable=False, comment="防重复提交的客户端令牌")
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, comment="规范化输入指纹")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("owner_id", "client_token", name="uq_opp_ip_research_owner_token"),
        Index("idx_opp_ip_research_owner_created", "owner_id", text("created_at DESC"), text("id DESC")),
        Index("idx_opp_ip_research_workspace", "workspace_id"),
        {"schema": _SCHEMA, "comment": "机会中心专利调研：产品信息、检索法域、销售市场"},
    )


class OppIpTask(Base):
    """一次需要外部调用的任务。调用期间不持有数据库会话。"""

    __tablename__ = "opp_ip_task"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    research_id: Mapped[int] = mapped_column(BigInteger, _fk("opp_ip_research"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, comment="plan/search/detail/analyze/ask")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="queued",
                                        comment="queued/running/completed/failed/unknown")
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, comment="固定的任务参数")
    planned_mcp_calls: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", comment="入队时核对预算用的计划 MCP 调用数",
    )
    client_token: Mapped[str] = mapped_column(String(64), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    error_category: Mapped[str | None] = mapped_column(String(32), comment="失败或未知的分类")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="运行租约到期时间")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("research_id", "client_token", name="uq_opp_ip_task_research_token"),
        CheckConstraint(_in("kind", TASK_KINDS), name="ck_opp_ip_task_kind"),
        CheckConstraint(_in("status", TASK_STATUSES), name="ck_opp_ip_task_status"),
        # 同一调研同时至多一个在途任务：防连点重复检索、预算核对被并发绕过
        Index("uq_opp_ip_task_active", "research_id", unique=True,
              postgresql_where=text("status IN ('queued', 'running')")),
        Index("idx_opp_ip_task_queued", "created_at", "id", postgresql_where=text("status = 'queued'")),
        Index("idx_opp_ip_task_research", "research_id", "id"),
        {"schema": _SCHEMA, "comment": "IP 专家任务：持久化队列，SKIP LOCKED 认领 + 租约"},
    )


class OppIpCall(Base):
    """一次外部调用（MCP 工具或大模型）。没有可靠的计费回执，``billing`` 恒为 unknown。"""

    __tablename__ = "opp_ip_call"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    research_id: Mapped[int] = mapped_column(BigInteger, _fk("opp_ip_research"), nullable=False)
    task_id: Mapped[int] = mapped_column(BigInteger, _fk("opp_ip_task"), nullable=False)
    provider: Mapped[str] = mapped_column(String(8), nullable=False, comment="mcp/llm")
    tool: Mapped[str] = mapped_column(String(64), nullable=False, comment="MCP 工具名，或 llm 的 plan/analysis")
    args: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, comment="参数摘要（不含 Key 与提示词全文）")
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, comment="success/error/unknown")
    http_status: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[int | None] = mapped_column(BigInteger, comment="供应商 error_code")
    error_category: Mapped[str | None] = mapped_column(String(32))
    billing: Mapped[str] = mapped_column(String(16), nullable=False, server_default="unknown",
                                         comment="计费状态：无可靠回执，恒为 unknown")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(_in("provider", PROVIDERS), name="ck_opp_ip_call_provider"),
        CheckConstraint(_in("outcome", CALL_OUTCOMES), name="ck_opp_ip_call_outcome"),
        Index("idx_opp_ip_call_research", "research_id", "provider"),
        # 全站每日预算按 started_at 计数
        Index("idx_opp_ip_call_provider_started", "provider", "started_at"),
        {"schema": _SCHEMA, "comment": "IP 专家外部调用留痕（MCP 预算按它计数）"},
    )


class OppIpQuery(Base):
    """一条执行过的检索式及结果。"""

    __tablename__ = "opp_ip_query"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    research_id: Mapped[int] = mapped_column(BigInteger, _fk("opp_ip_research"), nullable=False)
    task_id: Mapped[int] = mapped_column(BigInteger, _fk("opp_ip_task"), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False, comment="人工确认的检索式")
    limit: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, comment="success/failed/unknown")
    error_category: Mapped[str | None] = mapped_column(String(32))
    total_count: Mapped[int | None] = mapped_column(Integer, comment="来源匹配总数")
    returned_count: Mapped[int | None] = mapped_column(Integer, comment="本次实际返回条数")
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="供应商原始 data")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(_in("status", QUERY_STATUSES), name="ck_opp_ip_query_status"),
        Index("idx_opp_ip_query_research", "research_id", "id"),
        {"schema": _SCHEMA, "comment": "IP 专家检索式与结果"},
    )


class OppIpCandidate(Base):
    """调研内按 patent_id 去重的候选专利。"""

    __tablename__ = "opp_ip_candidate"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    research_id: Mapped[int] = mapped_column(BigInteger, _fk("opp_ip_research"), nullable=False)
    query_id: Mapped[int] = mapped_column(BigInteger, _fk("opp_ip_query"), nullable=False, comment="首次出现的检索式")
    rank: Mapped[int] = mapped_column(Integer, nullable=False, comment="首次出现时在该检索式结果中的位置（从 1 起）")
    patent_id: Mapped[str] = mapped_column(String(64), nullable=False)
    pn: Mapped[str | None] = mapped_column(String(64), comment="公开（公告）号")
    title: Mapped[str | None] = mapped_column(Text)
    authority: Mapped[str | None] = mapped_column(String(16), comment="受理局")
    apno: Mapped[str | None] = mapped_column(String(64))
    apdt: Mapped[int | None] = mapped_column(Integer, comment="申请日 yyyyMMdd")
    pbdt: Mapped[int | None] = mapped_column(Integer, comment="公开日 yyyyMMdd")
    assignee: Mapped[str | None] = mapped_column(Text, comment="当前权利人")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("research_id", "patent_id", name="uq_opp_ip_candidate_research_patent"),
        Index("idx_opp_ip_candidate_research", "research_id", "id"),
        {"schema": _SCHEMA, "comment": "IP 专家候选专利"},
    )


class OppIpDetail(Base):
    """候选的单件详情。成功结果不重复取。"""

    __tablename__ = "opp_ip_detail"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(BigInteger, _fk("opp_ip_candidate"), nullable=False)
    tool: Mapped[str] = mapped_column(String(32), nullable=False,
                                      comment="get_patent_legal_status/bibliography/abstract_image")
    status: Mapped[str] = mapped_column(String(16), nullable=False, comment="success/failed/unknown/missing")
    task_id: Mapped[int] = mapped_column(BigInteger, _fk("opp_ip_task"), nullable=False)
    error_category: Mapped[str | None] = mapped_column(String(32))
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="规范化字段 + 原始条目")
    image: Mapped[bytes | None] = mapped_column(LargeBinary, comment="摘要附图本体（取证时下载；供应商地址约 1 小时失效）")
    image_content_type: Mapped[str | None] = mapped_column(String(32))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("candidate_id", "tool", name="uq_opp_ip_detail_candidate_tool"),
        CheckConstraint(_in("tool", DETAIL_TOOLS), name="ck_opp_ip_detail_tool"),
        CheckConstraint(_in("status", DETAIL_STATUSES), name="ck_opp_ip_detail_status"),
        {"schema": _SCHEMA, "comment": "IP 专家候选详情与摘要附图"},
    )


class OppIpAgentOutput(Base):
    """Agent 生成的检索方案或风险分析（经服务端校验）。与人工意见分开。"""

    __tablename__ = "opp_ip_agent_output"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    research_id: Mapped[int] = mapped_column(BigInteger, _fk("opp_ip_research"), nullable=False)
    task_id: Mapped[int] = mapped_column(BigInteger, _fk("opp_ip_task"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, comment="plan/analysis")
    model: Mapped[str] = mapped_column(String(100), nullable=False, comment="生成所用模型")
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, comment="校验后的结构化输出")
    coverage: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="分析时服务端算出的覆盖事实（不由模型生成）",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(_in("kind", OUTPUT_KINDS), name="ck_opp_ip_agent_output_kind"),
        Index("idx_opp_ip_agent_output_research", "research_id", "kind", "id"),
        {"schema": _SCHEMA, "comment": "IP 专家 Agent 输出（检索方案、风险分析）"},
    )


class OppIpReview(Base):
    """一条按市场的人工意见。只追加，不修改、不删除。"""

    __tablename__ = "opp_ip_review"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    research_id: Mapped[int] = mapped_column(BigInteger, _fk("opp_ip_research"), nullable=False)
    market: Mapped[str] = mapped_column(String(8), nullable=False, comment="销售市场站点码")
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, comment="high/medium/low/undetermined")
    decision: Mapped[str] = mapped_column(
        String(24), nullable=False,
        comment="pass/conditional_pass/continue_verify/need_evidence/not_recommended",
    )
    basis: Mapped[str] = mapped_column(Text, nullable=False, comment="依据")
    uncovered_scope: Mapped[str] = mapped_column(Text, nullable=False, comment="未覆盖范围")
    conditions: Mapped[str | None] = mapped_column(Text, comment="整改条件")
    evidence_candidate_ids: Mapped[list[int]] = mapped_column(JSONB, nullable=False, comment="引用的候选 id")
    review_date: Mapped[date | None] = mapped_column(Date, comment="复核日期")
    signer_id: Mapped[int] = mapped_column(Integer, _fk("user_center", "RESTRICT"), nullable=False)
    client_token: Mapped[str] = mapped_column(String(64), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("research_id", "client_token", name="uq_opp_ip_review_research_token"),
        CheckConstraint(_in("risk_level", RISK_LEVELS), name="ck_opp_ip_review_risk_level"),
        CheckConstraint(_in("decision", DECISIONS), name="ck_opp_ip_review_decision"),
        Index("idx_opp_ip_review_research_market", "research_id", "market", "id"),
        {"schema": _SCHEMA, "comment": "IP 专家调研的人工意见（按市场，只追加）"},
    )


class OppIpMessage(Base):
    """任务对话里的一条消息：用户追问或 Agent 回答。调研时间线的其余内容由已存事实拼出。"""

    __tablename__ = "opp_ip_message"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    research_id: Mapped[int] = mapped_column(BigInteger, _fk("opp_ip_research"), nullable=False)
    task_id: Mapped[int] = mapped_column(BigInteger, _fk("opp_ip_task"), nullable=False,
                                         comment="用户消息对应它触发的 ask 任务；Agent 回答对应生成它的任务")
    role: Mapped[str] = mapped_column(String(8), nullable=False, comment="user/agent")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(String(100), comment="Agent 回答所用模型")
    cited_candidate_ids: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), comment="回答引用的本调研候选 id",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(_in("role", MESSAGE_ROLES), name="ck_opp_ip_message_role"),
        Index("idx_opp_ip_message_research", "research_id", "id"),
        {"schema": _SCHEMA, "comment": "IP 专家任务对话：用户追问与 Agent 回答"},
    )


class OppIpSchedule(Base):
    """定时复检：到点用已成功执行过的检索式重新检索。每个调研至多一条。"""

    __tablename__ = "opp_ip_schedule"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    research_id: Mapped[int] = mapped_column(BigInteger, _fk("opp_ip_research"), nullable=False)
    frequency: Mapped[str] = mapped_column(String(16), nullable=False, comment="weekly/monthly")
    weekday: Mapped[int | None] = mapped_column(Integer, comment="weekly：0=周一 … 6=周日")
    day_of_month: Mapped[int | None] = mapped_column(Integer, comment="monthly：1–28")
    hour: Mapped[int] = mapped_column(Integer, nullable=False, comment="执行时刻（Asia/Shanghai，0–23 点）")
    queries: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False,
                                                         comment="复检的检索式 [{query_text, limit}]")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="下次执行时间")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="最近一次到期处理时间")
    last_status: Mapped[str | None] = mapped_column(String(16), comment="queued/skipped")
    last_reason: Mapped[str | None] = mapped_column(Text, comment="跳过原因")
    last_task_id: Mapped[int | None] = mapped_column(BigInteger, _fk("opp_ip_task", "SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("research_id", name="uq_opp_ip_schedule_research"),
        CheckConstraint(_in("frequency", SCHEDULE_FREQUENCIES), name="ck_opp_ip_schedule_frequency"),
        CheckConstraint("hour BETWEEN 0 AND 23", name="ck_opp_ip_schedule_hour"),
        CheckConstraint(
            "(frequency = 'weekly' AND weekday BETWEEN 0 AND 6 AND day_of_month IS NULL) OR "
            "(frequency = 'monthly' AND day_of_month BETWEEN 1 AND 28 AND weekday IS NULL)",
            name="ck_opp_ip_schedule_when",
        ),
        CheckConstraint(f"last_status IS NULL OR {_in('last_status', SCHEDULE_RUN_STATUSES)}",
                        name="ck_opp_ip_schedule_last_status"),
        Index("idx_opp_ip_schedule_due", "next_run_at", postgresql_where=text("enabled")),
        {"schema": _SCHEMA, "comment": "IP 专家定时复检"},
    )
