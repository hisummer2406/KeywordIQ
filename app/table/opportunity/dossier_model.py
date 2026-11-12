"""机会档案的持久化模型（change add-opportunity-dossier）。

五张普通表，**不分区、不登记进 partitioning.PARTITIONED_TABLES**：证据快照与已签署的决定是
选品依据，按月 DROP 会让决定失去出处。只读 ABA 事实表，不写。

- ``opp_dossier``：档案。团队可见（持有 ``opportunity:view`` 即可读写），不可删除只可归档。
  ``status`` / ``recheck_date`` 是最新一条决定的冗余，与决定同事务写入，供列表筛选。
- ``opp_dossier_keyword``：档案的关键词。``keyword_id`` 为空表示该站点 ``dim_keyword`` 里没有这个词。
  刻意不建到 ``dim_keyword`` 的外键：维度行被清理或测试夹具 TRUNCATE 时，档案仍保留词文本。
- ``opp_dossier_evidence``：只追加的证据。``aba_snapshot`` 是确定性快照；``manual`` 是带来源的人工证据，
  只能作废。
- ``opp_dossier_decision``：只追加的决定。
- ``opp_dossier_message``：追问。一行同时是问题与它的回答，也是持久化队列的任务行。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.table.analysis.column_comments import SITE_COLUMN_COMMENT
from app.user import user_model  # noqa: F401 - 外键目标 user_center 须先注册进同一 metadata
from config import settings
from database import Base

_SCHEMA = settings.DATABASE_SCHEMA

#: 决定。档案状态 = 最新一条决定；没有决定时为 researching。
VERDICTS = ("advance", "need_evidence", "watch", "reject")
DOSSIER_STATUSES = ("researching", *VERDICTS)
#: 必须带复查日期的决定
RECHECK_REQUIRED = ("need_evidence", "watch")

EVIDENCE_KINDS = ("aba_snapshot", "manual")
#: 未接入的数据类别（固定缺口清单）+ 其他。外观专利不在这里，由 IP 调研承接。
GAP_CATEGORIES = ("search_volume", "sales", "price", "bsr", "reviews", "supply_cost", "trademark")
MANUAL_CATEGORIES = (*GAP_CATEGORIES, "other")

MESSAGE_STATUSES = ("queued", "running", "completed", "failed", "unknown")
ACTIVE_MESSAGE_STATUSES = ("queued", "running")

MAX_KEYWORDS = 20


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


def _fk(table: str, ondelete: str = "CASCADE") -> ForeignKey:
    return ForeignKey(f"{_SCHEMA}.{table}.id", ondelete=ondelete)


def _user(comment: str, *, nullable: bool = False) -> Mapped[Any]:
    return mapped_column(Integer, _fk("user_center", "RESTRICT"), nullable=nullable, comment=comment)


class OppDossier(Base):
    """机会档案：一个产品想法 + 一个站点 + 一组关键词。"""

    __tablename__ = "opp_dossier"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(100), nullable=False, comment="标题")
    idea: Mapped[str | None] = mapped_column(Text, comment="产品想法说明")
    site: Mapped[str] = mapped_column(String(8), nullable=False, comment=SITE_COLUMN_COMMENT)
    creator_id: Mapped[int] = _user("创建人")
    assignee_id: Mapped[int] = _user("负责人；任何成员可接手")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'researching'"),
                                        comment="最新决定的冗余：researching/advance/need_evidence/watch/reject")
    recheck_date: Mapped[date | None] = mapped_column(Date, comment="最新决定的复查日期（冗余）")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="归档时间；空即未归档")
    client_token: Mapped[str] = mapped_column(String(64), nullable=False, comment="防重复提交的客户端令牌")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(),
                                                 comment="最近一次写入（证据、决定、关键词、信息）")

    __table_args__ = (
        UniqueConstraint("creator_id", "client_token", name="uq_opp_dossier_creator_token"),
        CheckConstraint(_in("status", DOSSIER_STATUSES), name="ck_opp_dossier_status"),
        Index("idx_opp_dossier_updated", text("updated_at DESC"), text("id DESC")),
        Index("idx_opp_dossier_recheck", "recheck_date", postgresql_where=text("archived_at IS NULL")),
        {"schema": _SCHEMA, "comment": "机会档案（团队可见，不可删除只可归档）"},
    )


class OppDossierKeyword(Base):
    """档案里的一个关键词。"""

    __tablename__ = "opp_dossier_keyword"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dossier_id: Mapped[int] = mapped_column(BigInteger, _fk("opp_dossier"), nullable=False)
    keyword: Mapped[str] = mapped_column(String(500), nullable=False, comment="规范化后的关键词（小写、单空格）")
    keyword_id: Mapped[int | None] = mapped_column(
        BigInteger, comment="→ dim_keyword（不建外键）；空 = 该站点保留期内未进入过 ABA 榜单",
    )
    added_by: Mapped[int] = _user("添加人")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("dossier_id", "keyword", name="uq_opp_dossier_keyword"),
        {"schema": _SCHEMA, "comment": "机会档案的关键词"},
    )


class OppDossierEvidence(Base):
    """一条证据。ABA 快照只追加；人工证据只能作废。"""

    __tablename__ = "opp_dossier_evidence"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dossier_id: Mapped[int] = mapped_column(BigInteger, _fk("opp_dossier"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, comment="aba_snapshot/manual")
    # ---- aba_snapshot ----
    anchor_day: Mapped[date | None] = mapped_column(Date, comment="快照时该站点日榜已导入的最新期")
    anchor_week: Mapped[date | None] = mapped_column(Date, comment="快照时该站点周榜已导入的最新期")
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="快照内容（见 dossier_evidence.py）")
    # ---- manual ----
    category: Mapped[str | None] = mapped_column(String(16), comment="人工证据的类别")
    content: Mapped[str | None] = mapped_column(Text, comment="人工证据内容")
    source_name: Mapped[str | None] = mapped_column(String(100), comment="来源名称")
    source_url: Mapped[str | None] = mapped_column(String(500), comment="来源链接")
    observed_date: Mapped[date | None] = mapped_column(Date, comment="观测日期")
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="作废时间")
    voided_by: Mapped[int | None] = _user("作废人", nullable=True)
    created_by: Mapped[int] = _user("生成人 / 填写人")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(_in("kind", EVIDENCE_KINDS), name="ck_opp_dossier_evidence_kind"),
        CheckConstraint(
            "(kind = 'aba_snapshot' AND payload IS NOT NULL) OR "
            f"(kind = 'manual' AND {_in('category', MANUAL_CATEGORIES)} AND content IS NOT NULL "
            "AND source_name IS NOT NULL AND observed_date IS NOT NULL)",
            name="ck_opp_dossier_evidence_shape",
        ),
        Index("idx_opp_dossier_evidence_dossier", "dossier_id", "kind", text("id DESC")),
        {"schema": _SCHEMA, "comment": "机会档案证据：ABA 快照（只追加）与人工证据（只能作废）"},
    )


class OppDossierDecision(Base):
    """一条决定。只追加。"""

    __tablename__ = "opp_dossier_decision"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dossier_id: Mapped[int] = mapped_column(BigInteger, _fk("opp_dossier"), nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False, comment="advance/need_evidence/watch/reject")
    reason: Mapped[str] = mapped_column(Text, nullable=False, comment="理由")
    next_step: Mapped[str | None] = mapped_column(Text, comment="下一步")
    recheck_date: Mapped[date | None] = mapped_column(Date, comment="复查日期")
    snapshot_id: Mapped[int | None] = mapped_column(
        BigInteger, _fk("opp_dossier_evidence", "RESTRICT"), comment="签署时最新的 ABA 快照",
    )
    ip_status: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, comment="签署时的专利核查状态摘要")
    pricing_status: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="签署时关联核价单的测算摘要（change add-opportunity-feasibility；此前的决定为空）")
    signer_id: Mapped[int] = _user("签署人")
    client_token: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("dossier_id", "client_token", name="uq_opp_dossier_decision_token"),
        CheckConstraint(_in("verdict", VERDICTS), name="ck_opp_dossier_decision_verdict"),
        Index("idx_opp_dossier_decision_dossier", "dossier_id", "id"),
        {"schema": _SCHEMA, "comment": "机会档案的决定（只追加）"},
    )


class OppDossierMessage(Base):
    """一次追问：问题、回答与队列状态在同一行。"""

    __tablename__ = "opp_dossier_message"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dossier_id: Mapped[int] = mapped_column(BigInteger, _fk("opp_dossier"), nullable=False)
    asker_id: Mapped[int] = _user("提问人")
    question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'queued'"))
    error_category: Mapped[str | None] = mapped_column(String(32), comment="失败或未知的分类")
    answer: Mapped[str | None] = mapped_column(Text)
    cited_evidence_ids: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), comment="回答引用的本档案证据 id",
    )
    model: Mapped[str | None] = mapped_column(String(100))
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="大模型用量（供应商回报）")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="执行租约")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(_in("status", MESSAGE_STATUSES), name="ck_opp_dossier_message_status"),
        Index("idx_opp_dossier_message_dossier", "dossier_id", "id"),
        # 同一档案同时至多一个在途问题
        Index("uq_opp_dossier_message_active", "dossier_id", unique=True,
              postgresql_where=text("status IN ('queued', 'running')")),
        Index("idx_opp_dossier_message_queue", "id", postgresql_where=text("status = 'queued'")),
        {"schema": _SCHEMA, "comment": "机会档案追问：问题、回答与队列状态"},
    )
