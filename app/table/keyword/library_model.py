"""词库：用户自建的关键词清单。

两张普通表——不分区、不参与保留清理、不被导入路径写入。它们是本仓库里第一份**用户
产生**的业务数据（此前所有业务表的内容都来自报表导入），因此几条约定与事实表那边不同：

- **条目只存 ``keyword_id``**，不存关键词文本、也不存站点码：``dim_keyword`` 是
  ``(site, keyword)`` 唯一，一个 id 已经把两者都确定了。另存副本就是给同一个词开第二份
  事实来源，而两份必然分叉——``sites`` 参考表与 ``GB``/``UK`` 两种写法都是这么来的。
- **外键指向 ``dim_keyword`` 永不悬空**：保留清理只 ``DROP`` 事实表的月度分区，维度表
  不参与。清单里的词过了保留窗口不会消失，只会在盯盘时显示为「该期无数据」。
- **分组绑定站点且不可改**：组内条目全部以站点为前提，改站点等于把内容整个换掉，
  那不如新建一个组。

schema 由 ``database.Base`` 的 metadata 统一提供（取自 ``settings.DATABASE_SCHEMA``）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.table.analysis.column_comments import SITE_COLUMN_COMMENT
from config import settings
from database import Base


class KeywordGroup(Base):
    """关键词分组：一个用户在一个站点下的一份清单。

    私有性由查询条件里的 ``user_id`` 保证，**不靠界面不展示**。取不到自己的分组一律
    404 而非 403——403 会泄露「这个 id 确实存在」。
    """

    __tablename__ = "keyword_group"

    group_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey(f"{settings.DATABASE_SCHEMA}.user_center.id", ondelete="CASCADE"),
        nullable=False,
        comment="→ user_center.id，分组的唯一归属人",
    )
    site: Mapped[str] = mapped_column(String(8), nullable=False, comment=SITE_COLUMN_COMMENT)
    name: Mapped[str] = mapped_column(String(50), nullable=False, comment="分组名，同一用户同一站点下唯一")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "site", "name", name="uq_keyword_group_user_site_name"),
        # 分组列表按 (用户, 站点) 取，条数受上限约束（每站点至多 20 组），索引前缀即查询条件。
        Index("idx_keyword_group_owner", "user_id", "site"),
        {"comment": "词库分组：用户自建的关键词清单，绑定单一站点且站点建后不可改。"},
    )


class KeywordGroupItem(Base):
    """分组条目：一个关键词在一个分组里的一条记录。

    复合主键 ``(group_id, keyword_id)`` 顺带把去重做掉了——批量加词走
    ``ON CONFLICT DO NOTHING``，天生幂等，不必先查后写。

    **每组条目数上限（``LIBRARY_MAX_ITEMS_PER_GROUP``）不只是产品判断，也是盯盘查询的
    规模保证**：那条查询由本表驱动、LEFT JOIN 事实表与掉榜表，最坏情况就是上限那么多行
    的嵌套循环。放宽上限前必须重测它。执行点在 service 层（超限 422、整批不写）。
    """

    __tablename__ = "keyword_group_item"

    group_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{settings.DATABASE_SCHEMA}.keyword_group.group_id", ondelete="CASCADE"),
        primary_key=True,
        comment="→ keyword_group.group_id",
    )
    keyword_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{settings.DATABASE_SCHEMA}.dim_keyword.keyword_id", ondelete="CASCADE"),
        primary_key=True,
        comment="→ dim_keyword.keyword_id，已唯一确定 (站点, 关键词)",
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        {"comment": "词库条目：只存 keyword_id，不另存关键词文本与站点码（避免第二份事实来源）。"},
    )
