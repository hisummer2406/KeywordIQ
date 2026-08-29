"""竞对卖家：共享事实层。

``CompetitorSeller`` 是全局唯一的卖家主体（``(seller_id, site)`` 复合主键），MUST NOT
携带账号属性——同一卖家被多个用户关注时仍是同一行，账号私有的关注关系记录在
``watch_model.UserSellerWatch``。

``baseline_at`` 是为后续数据获取层（浏览器插件/Keepa，不在本变更范围）预留的列：本变更
只写 NULL，状态恒为「待建立基线」。首刷时全部 ASIN 都是「首次见到」但不是新品，
只有 ``baseline_at`` 落定之后、``first_seen_at > baseline_at`` 的才算真正的新品——这条
判定逻辑属于数据获取层，本变更不实现。

schema 由 ``database.Base`` 的 metadata 统一提供（取自 ``settings.DATABASE_SCHEMA``）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, PrimaryKeyConstraint, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.table.analysis.column_comments import SITE_COLUMN_COMMENT
from database import Base


class CompetitorSeller(Base):
    """卖家主体：``(seller_id, site)`` 全局唯一，抓取只做一次，不因谁在看而重复存储。"""

    __tablename__ = "competitor_seller"

    seller_id: Mapped[str] = mapped_column(
        String(24), primary_key=True, comment="亚马逊卖家 ID，形如 A1TZ946F8HZTNY"
    )
    site: Mapped[str] = mapped_column(String(8), primary_key=True, comment=SITE_COLUMN_COMMENT)
    baseline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="基线建立时间，NULL 表示尚未建立（本变更恒为 NULL，由数据获取层写入）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        PrimaryKeyConstraint("seller_id", "site", name="pk_competitor_seller"),
        {"comment": "竞对卖家主体：全局共享，不含账号属性；关注关系见 user_seller_watch。"},
    )
