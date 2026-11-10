"""定价核算的持久化模型（change add-pricing-calculation D8、D9）。

五张普通表，**不分区、不登记进 partitioning.PARTITIONED_TABLES**：核价历史与费率版本是
复核依据，按月 DROP 会让已保存方案无从复算。不与 ABA 事实表关联，不被导入路径写入。

- ``pricing_rule``：共享费用规则的版本表。五类规则形状差异大、官方费率年年改结构，
  载荷存 JSONB，由 ``rule_schemas`` 按 ``kind`` 严格校验（D8）。
- ``pricing_sheet`` / ``pricing_sheet_version``：核价单与不可变版本快照。私有性靠查询
  条件里的 ``owner_id``，取不到一律 404。
- ``pricing_selection`` / ``pricing_selection_log``：每个市场一条当前选定，换选留痕。
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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.user import user_model  # noqa: F401 - 外键目标 user_center 须先注册进同一 metadata
from config import settings
from database import Base

_SCHEMA = settings.DATABASE_SCHEMA

RULE_KINDS = ("fx_rate", "market_params", "referral_schedule", "fba_rate_card", "fbm_channel")
RULE_STATUSES = ("draft", "active", "retired")


class PricingRule(Base):
    """一条共享费用规则的一个版本。已启用的版本不可原地修改（改要另建草稿）。"""

    __tablename__ = "pricing_rule"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(
        String(32), nullable=False,
        comment="规则类别：fx_rate/market_params/referral_schedule/fba_rate_card/fbm_channel",
    )
    scope_key: Mapped[str] = mapped_column(
        String(64), nullable=False,
        comment="适用范围：站点码（如 US），汇率为币种（如 USD），自发货渠道为 站点:渠道码",
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, comment="同一 (kind, scope_key) 内递增")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="draft",
                                        comment="draft/active/retired")
    effective_from: Mapped[date] = mapped_column(Date, nullable=False, comment="生效日（含）")
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True, comment="失效日（含），NULL 为长期")
    source: Mapped[str] = mapped_column(Text, nullable=False, comment="来源：官方文档 URL、报价单或原表单元格")
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True, comment="审核说明，冲突与待核实项")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, comment="按 kind 校验的规则载荷")
    payload_schema: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1",
                                                comment="载荷结构版本")
    retired_reason: Mapped[str | None] = mapped_column(Text, nullable=True, comment="停用原因")
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1",
                                              comment="乐观锁版本号")
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey(f"{_SCHEMA}.user_center.id", ondelete="SET NULL"), nullable=True, comment="→ user_center.id")
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey(f"{_SCHEMA}.user_center.id", ondelete="SET NULL"), nullable=True, comment="→ user_center.id")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("kind", "scope_key", "version", name="uq_pricing_rule_version"),
        CheckConstraint(f"kind IN {RULE_KINDS!r}", name="ck_pricing_rule_kind"),
        CheckConstraint(f"status IN {RULE_STATUSES!r}", name="ck_pricing_rule_status"),
        CheckConstraint("effective_to IS NULL OR effective_to >= effective_from", name="ck_pricing_rule_period"),
        Index("idx_pricing_rule_lookup", "kind", "scope_key", "status"),
        {"comment": "核价共享费用规则的版本表：草稿可改，启用后不可原地修改，停用保留历史。"},
    )


class PricingSheet(Base):
    """核价单：创建人私有。草稿只存输入；保存产生不可变版本。"""

    __tablename__ = "pricing_sheet"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey(f"{_SCHEMA}.user_center.id", ondelete="CASCADE"), nullable=False,
        comment="→ user_center.id，唯一归属人；他人访问一律 404")
    product_name: Mapped[str] = mapped_column(String(200), nullable=False, comment="产品名称")
    sku: Mapped[str | None] = mapped_column(String(100), nullable=True)
    asin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True, comment="来源链接")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="draft",
                                        comment="draft（未保存过版本）/saved")
    draft_input: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True,
                                                               comment="当前草稿输入，可反复修改")
    current_version_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True,
                                                           comment="最新已保存版本 id")
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1",
                                              comment="乐观锁版本号，并发修改草稿返回 409")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('draft', 'saved')", name="ck_pricing_sheet_status"),
        Index("idx_pricing_sheet_owner", "owner_id", "updated_at"),
        {"comment": "核价单：创建人私有，草稿可改，保存产生不可变版本。"},
    )


class PricingSheetVersion(Base):
    """核价单的不可变版本快照：输入、采用的规则与参数、结果、模型版本。"""

    __tablename__ = "pricing_sheet_version"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sheet_id: Mapped[int] = mapped_column(
        ForeignKey(f"{_SCHEMA}.pricing_sheet.id", ondelete="CASCADE"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, comment="核价单内从 1 递增")
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, comment="核价输入快照")
    rule_refs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False,
                                                 comment="采用的规则 id/版本/范围/来源与参数副本")
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, comment="服务端计算结果快照")
    model_version: Mapped[str] = mapped_column(String(16), nullable=False, comment="计算模型版本")
    calc_date: Mapped[date] = mapped_column(Date, nullable=False, comment="核价日期，规则按它命中")
    client_token: Mapped[str] = mapped_column(String(64), nullable=False, comment="防重复提交的客户端令牌")
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey(f"{_SCHEMA}.user_center.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())

    __table_args__ = (
        UniqueConstraint("sheet_id", "version_no", name="uq_pricing_sheet_version_no"),
        UniqueConstraint("sheet_id", "client_token", name="uq_pricing_sheet_version_token"),
        {"comment": "核价版本快照：保存后不可修改，规则更新不改变历史金额。"},
    )


class PricingSelection(Base):
    """每个核价单每个市场的当前选定方案。"""

    __tablename__ = "pricing_selection"

    sheet_id: Mapped[int] = mapped_column(
        ForeignKey(f"{_SCHEMA}.pricing_sheet.id", ondelete="CASCADE"), primary_key=True)
    market: Mapped[str] = mapped_column(String(8), primary_key=True, comment="站点码")
    version_id: Mapped[int] = mapped_column(
        ForeignKey(f"{_SCHEMA}.pricing_sheet_version.id", ondelete="CASCADE"), nullable=False)
    option_key: Mapped[str] = mapped_column(String(80), nullable=False, comment="FBA 或 FBM:<渠道码>")
    note: Mapped[str | None] = mapped_column(Text, nullable=True, comment="选定说明")
    selected_by: Mapped[int | None] = mapped_column(
        ForeignKey(f"{_SCHEMA}.user_center.id", ondelete="SET NULL"), nullable=True)
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  server_default=func.now())

    __table_args__ = ({"comment": "核价选定方案：每个市场一条，换选写 pricing_selection_log。"},)


class PricingSelectionLog(Base):
    """选定方案的变更记录。"""

    __tablename__ = "pricing_selection_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sheet_id: Mapped[int] = mapped_column(
        ForeignKey(f"{_SCHEMA}.pricing_sheet.id", ondelete="CASCADE"), nullable=False)
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    version_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    option_key: Mapped[str] = mapped_column(String(80), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_by: Mapped[int | None] = mapped_column(
        ForeignKey(f"{_SCHEMA}.user_center.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())

    __table_args__ = (
        Index("idx_pricing_selection_log_sheet", "sheet_id", "market"),
        {"comment": "核价选定方案的留痕：每次选定或换选一行。"},
    )
