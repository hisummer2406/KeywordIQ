"""外观专利核查的持久化模型（change add-patent-risk-check）。

八张普通表，**不分区、不登记进 partitioning.PARTITIONED_TABLES**：核查证据与人工意见是
上架决策的依据，按月 DROP 会让已签署的意见失去出处。不与 ABA 事实表关联，不被导入路径写入。

- ``risk_check``：核查单，输入快照创建后不可改（换图/版本/市场 → 新一轮，``previous_check_id``）。
- ``risk_check_image``：原图本体（bytea，design 决策 2），只经鉴权端点读取。
- ``risk_task``：持久化任务，``SKIP LOCKED`` 认领 + 租约（决策 3）。
- ``risk_provider_call``：每一次供应商 HTTP 调用（决策 4），计费状态恒为未知。
- ``risk_search_page`` / ``risk_candidate``：每页检索证据与去重后的候选（决策 6）。
- ``risk_patent_detail``：按需获取的著录 / 法律状态（决策 7）。
- ``risk_review``：按市场追加的人工意见（决策 10）。

私有性靠每条查询带 ``risk_check.owner_id``，取不到一律 404——超管也不例外。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Float,
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

from app.user import user_model  # noqa: F401 - 外键目标 user_center 须先注册进同一 metadata
from config import settings
from database import Base

_SCHEMA = settings.DATABASE_SCHEMA

TASK_KINDS = ("search", "detail")
TASK_STATUSES = ("queued", "running", "completed", "failed", "unknown")
ACTIVE_TASK_STATUSES = ("queued", "running")
CALL_OUTCOMES = ("success", "error", "unknown")
DETAIL_KINDS = ("bibliography", "legal")
DETAIL_STATUSES = ("success", "failed", "unknown", "missing")
RISK_LEVELS = ("high", "medium", "low", "undetermined")
DECISIONS = ("pass", "conditional_pass", "continue_verify", "need_evidence", "not_recommended")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


class RiskCheck(Base):
    """一次外观专利核查。输入快照创建后不可修改。"""

    __tablename__ = "risk_check"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{_SCHEMA}.user_center.id", ondelete="RESTRICT"), nullable=False,
        comment="创建人；记录按创建人私有，他人（含超管）访问一律 404",
    )
    product_name: Mapped[str] = mapped_column(String(200), nullable=False, comment="产品名称")
    product_version: Mapped[str] = mapped_column(String(100), nullable=False, comment="产品版本")
    source_ref: Mapped[str | None] = mapped_column(String(500), comment="来源链接或 ASIN 备注")
    description: Mapped[str | None] = mapped_column(Text, comment="产品说明")
    markets: Mapped[list[str]] = mapped_column(JSONB, nullable=False, comment="销售市场站点码列表")
    jurisdictions: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, comment="用户明确勾选的检索法域编码列表（与销售市场相互独立）",
    )
    page_size: Mapped[int] = mapped_column(Integer, nullable=False, comment="每页读取条数：10/20/50")
    previous_check_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{_SCHEMA}.risk_check.id", ondelete="SET NULL"),
        comment="上一轮核查（同一创建人）",
    )
    client_token: Mapped[str] = mapped_column(String(64), nullable=False, comment="防重复提交的客户端令牌")
    input_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="规范化输入 + 图片 sha256 的指纹；同令牌不同指纹 → 409",
    )
    search_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="queued",
        comment="最近一个检索任务的状态：queued/running/completed/failed/unknown",
    )
    fetched_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", comment="已读取到的来源结果位置（下一页 offset）",
    )
    total_count: Mapped[int | None] = mapped_column(Integer, comment="供应商最近一次返回的来源匹配总数")
    upload_url: Mapped[str | None] = mapped_column(Text, comment="P010 返回的临时图片地址（约 9 小时过期）")
    upload_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="临时地址过期时间")
    searched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="最近一次检索完成时间")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("owner_id", "client_token", name="uq_risk_check_owner_token"),
        CheckConstraint(_in("search_status", TASK_STATUSES), name="ck_risk_check_search_status"),
        Index("idx_risk_check_owner_created", "owner_id", text("created_at DESC"), text("id DESC")),
        {"schema": _SCHEMA, "comment": "外观专利核查单：产品版本快照、销售市场、检索法域"},
    )


class RiskCheckImage(Base):
    """核查原图。单图不超过 4096 KB，按文件头判断的实际格式。"""

    __tablename__ = "risk_check_image"

    check_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{_SCHEMA}.risk_check.id", ondelete="CASCADE"), primary_key=True,
    )
    content_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="image/jpeg 或 image/png")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, comment="字节数")
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, comment="内容 sha256")
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, comment="原图内容")

    __table_args__ = ({"schema": _SCHEMA, "comment": "核查原图（只经鉴权端点读取）"},)


class RiskTask(Base):
    """一次需要调用供应商的任务。调用期间不持有数据库事务。"""

    __tablename__ = "risk_task"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    check_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{_SCHEMA}.risk_check.id", ondelete="CASCADE"), nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False, comment="search（检索一页）/detail（著录与法律状态）")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="queued",
                                        comment="queued/running/completed/failed/unknown")
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, comment="固定的任务参数")
    client_token: Mapped[str] = mapped_column(String(64), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    error_category: Mapped[str | None] = mapped_column(String(32), comment="失败或未知的分类")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="运行租约到期时间")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("check_id", "client_token", name="uq_risk_task_check_token"),
        CheckConstraint(_in("kind", TASK_KINDS), name="ck_risk_task_kind"),
        CheckConstraint(_in("status", TASK_STATUSES), name="ck_risk_task_status"),
        # 同一核查单同一类任务同时至多一个在途：防连点重复翻页 / 重复取详情（决策 8）
        Index("uq_risk_task_active", "check_id", "kind", unique=True,
              postgresql_where=text("status IN ('queued', 'running')")),
        Index("idx_risk_task_queued", "created_at", "id", postgresql_where=text("status = 'queued'")),
        Index("idx_risk_task_check", "check_id", "id"),
        {"schema": _SCHEMA, "comment": "核查任务：持久化队列，SKIP LOCKED 认领 + 租约"},
    )


class RiskProviderCall(Base):
    """一次供应商 HTTP 调用。没有可靠的计费回执，``billing`` 恒为 unknown。"""

    __tablename__ = "risk_provider_call"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{_SCHEMA}.risk_task.id", ondelete="CASCADE"), nullable=False,
    )
    api: Mapped[str] = mapped_column(String(8), nullable=False, comment="P010/P060/P011/P013")
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, comment="success/error/unknown")
    http_status: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[int | None] = mapped_column(BigInteger, comment="供应商 error_code")
    error_category: Mapped[str | None] = mapped_column(String(32))
    billing: Mapped[str] = mapped_column(String(16), nullable=False, server_default="unknown",
                                         comment="计费状态：无可靠回执，恒为 unknown")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(_in("outcome", CALL_OUTCOMES), name="ck_risk_provider_call_outcome"),
        Index("idx_risk_provider_call_task", "task_id"),
        {"schema": _SCHEMA, "comment": "智慧芽调用留痕"},
    )


class RiskSearchPage(Base):
    """一页检索结果的证据快照。"""

    __tablename__ = "risk_search_page"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    check_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{_SCHEMA}.risk_check.id", ondelete="CASCADE"), nullable=False,
    )
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{_SCHEMA}.risk_task.id", ondelete="CASCADE"), nullable=False,
    )
    offset: Mapped[int] = mapped_column(Integer, nullable=False)
    limit: Mapped[int] = mapped_column(Integer, nullable=False)
    returned_count: Mapped[int] = mapped_column(Integer, nullable=False, comment="本页实际返回条数")
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, comment="来源匹配总数")
    request_params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, comment="不含图片地址的请求参数")
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, comment="供应商原始 data")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("idx_risk_search_page_check", "check_id", "offset"),
        {"schema": _SCHEMA, "comment": "检索分页证据"},
    )


class RiskCandidate(Base):
    """去重后的候选专利（核查单内按 patent_id 唯一）。"""

    __tablename__ = "risk_candidate"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    check_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{_SCHEMA}.risk_check.id", ondelete="CASCADE"), nullable=False,
    )
    page_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{_SCHEMA}.risk_search_page.id", ondelete="CASCADE"), nullable=False,
        comment="首次出现的页",
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False, comment="首次出现时在来源结果中的位置（从 1 起）")
    patent_id: Mapped[str] = mapped_column(String(64), nullable=False)
    patent_pn: Mapped[str | None] = mapped_column(String(64), comment="公开（公告）号")
    title: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text, comment="供应商附图地址（会过期，只记来源与取证时间）")
    score: Mapped[float | None] = mapped_column(Float, comment="来源相似度，只用于排序")
    authority: Mapped[str | None] = mapped_column(String(16), comment="受理局")
    apno: Mapped[str | None] = mapped_column(String(64))
    apdt: Mapped[int | None] = mapped_column(Integer, comment="申请日 yyyyMMdd")
    pbdt: Mapped[int | None] = mapped_column(Integer, comment="公开日 yyyyMMdd")
    assignee: Mapped[str | None] = mapped_column(Text, comment="当前权利人")
    loc: Mapped[list[str] | None] = mapped_column(JSONB, comment="洛迦诺分类")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("check_id", "patent_id", name="uq_risk_candidate_check_patent"),
        Index("idx_risk_candidate_check_rank", "check_id", "rank"),
        {"schema": _SCHEMA, "comment": "候选外观专利"},
    )


class RiskPatentDetail(Base):
    """候选的著录或法律状态。只对尚无成功结果的项发起调用，成功结果不重复取。"""

    __tablename__ = "risk_patent_detail"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{_SCHEMA}.risk_candidate.id", ondelete="CASCADE"), nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False, comment="bibliography/legal")
    status: Mapped[str] = mapped_column(String(16), nullable=False, comment="success/failed/unknown/missing")
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{_SCHEMA}.risk_task.id", ondelete="CASCADE"), nullable=False,
    )
    error_category: Mapped[str | None] = mapped_column(String(32))
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="规范化字段 + 原始条目")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("candidate_id", "kind", name="uq_risk_patent_detail_candidate_kind"),
        CheckConstraint(_in("kind", DETAIL_KINDS), name="ck_risk_patent_detail_kind"),
        CheckConstraint(_in("status", DETAIL_STATUSES), name="ck_risk_patent_detail_status"),
        {"schema": _SCHEMA, "comment": "候选专利的著录与法律状态"},
    )


class RiskReview(Base):
    """一条按市场的人工意见。只追加，不修改、不删除。"""

    __tablename__ = "risk_review"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    check_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{_SCHEMA}.risk_check.id", ondelete="CASCADE"), nullable=False,
    )
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
    signer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{_SCHEMA}.user_center.id", ondelete="RESTRICT"), nullable=False,
    )
    client_token: Mapped[str] = mapped_column(String(64), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("check_id", "client_token", name="uq_risk_review_check_token"),
        CheckConstraint(_in("risk_level", RISK_LEVELS), name="ck_risk_review_risk_level"),
        CheckConstraint(_in("decision", DECISIONS), name="ck_risk_review_decision"),
        Index("idx_risk_review_check_market", "check_id", "market", "id"),
        {"schema": _SCHEMA, "comment": "按市场的人工意见（只追加）"},
    )
