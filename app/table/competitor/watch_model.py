"""竞对关注：私有视角层 + 预览暂存。

两张私有表——不分区、不参与保留清理、不被导入路径写入。第一份**用户产生**的数据不是
本模块首创（词库 ``keyword_group``/``keyword_group_item`` 已经是），沿用同一条纪律：
私有性靠查询条件里的 ``user_id`` 保证，不靠界面不展示；取不到自己的数据一律 404
而非 403。

与词库不同的两点：

- **分组不绑定站点**：一个卖家的站点已由 ``CompetitorSeller`` 唯一确定，分组只是
  用户自己的归类标签，允许跨站点。
- **删除分组不删除关注关系**（``group_id`` 外键 ``ON DELETE SET NULL``）：分组是标签，
  关注关系是"我在盯这个卖家"这件事本身，误删分组不该连带取消关注。

``CompetitorWatchPreview`` 是导入两步流的技术表，替代引入 Redis——过期行清理挂在
``app/table/upload/import_queue.py`` 已有的轮询循环上，不新开定时器。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from config import settings
from database import Base

_SCHEMA = settings.DATABASE_SCHEMA


class UserSellerGroup(Base):
    """卖家分组：一个用户自建的归类标签，不绑定站点。

    私有性由查询条件里的 ``user_id`` 保证。同一用户下分组名唯一，跨用户同名允许。
    """

    __tablename__ = "user_seller_group"

    group_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey(f"{_SCHEMA}.user_center.id", ondelete="CASCADE"),
        nullable=False,
        comment="→ user_center.id，分组的唯一归属人",
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False, comment="分组名，同一用户下唯一")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_user_seller_group_user_name"),
        Index("idx_user_seller_group_owner", "user_id"),
        {"comment": "竞对卖家分组：用户自建归类标签，不绑定站点，删组不连带删除关注关系。"},
    )


class UserSellerWatch(Base):
    """关注关系：一个用户对一个卖家的关注，私有。

    复合外键指向 ``CompetitorSeller(seller_id, site)``——关注的是一个具体站点的卖家，
    而非跨站点的抽象主体（已定案：多站点同卖家分开算，见 design.md 决策 1）。
    """

    __tablename__ = "user_seller_watch"

    watch_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey(f"{_SCHEMA}.user_center.id", ondelete="CASCADE"),
        nullable=False,
        comment="→ user_center.id，关注关系的唯一归属人",
    )
    seller_id: Mapped[str] = mapped_column(String(24), nullable=False, comment="→ competitor_seller.seller_id")
    site: Mapped[str] = mapped_column(String(8), nullable=False, comment="→ competitor_seller.site")
    group_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(f"{_SCHEMA}.user_seller_group.group_id", ondelete="SET NULL"),
        nullable=True,
        comment="→ user_seller_group.group_id，NULL 表示未分组",
    )
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1",
        comment="关注度信号，导入时取书签重复收藏次数，默认 1",
    )
    note: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="用户备注")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["seller_id", "site"],
            [f"{_SCHEMA}.competitor_seller.seller_id", f"{_SCHEMA}.competitor_seller.site"],
            name="fk_user_seller_watch_seller",
        ),
        UniqueConstraint("user_id", "seller_id", "site", name="uq_user_seller_watch_user_seller"),
        # 主表格按 (用户, 分组) 取，分组筛选是最常见的查询路径。
        Index("idx_user_seller_watch_owner", "user_id", "group_id"),
        {"comment": "竞对卖家关注关系：账号私有，UNIQUE(user_id, seller_id, site)。"},
    )


class CompetitorWatchPreview(Base):
    """导入预览暂存：替代引入 Redis 的短期状态载体，不建索引即用于高频查询——只按主键取。"""

    __tablename__ = "competitor_watch_preview"

    preview_token: Mapped[str] = mapped_column(String(36), primary_key=True, comment="UUID4")
    user_id: Mapped[int] = mapped_column(
        ForeignKey(f"{_SCHEMA}.user_center.id", ondelete="CASCADE"),
        nullable=False,
        comment="→ user_center.id，预览的归属人；确认接口据此校验，非归属人一律 404",
    )
    parsed: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, comment="解析结果：new/existing/unparsable/groups 等，供 commit 落库"
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="过期时间，由轮询任务清理"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # 过期清理按时间扫描，量小（单用户几分钟内至多几条），加一个索引即可。
        Index("idx_competitor_watch_preview_expires", "expires_at"),
        {"comment": "竞对导入两步流的预览暂存，替代 Redis；过期行由轮询任务清理。"},
    )
