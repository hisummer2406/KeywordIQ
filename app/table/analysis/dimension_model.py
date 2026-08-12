"""星型模型的维度表。

拆出维度表的原因是原始数据里存在两类巨量重复：同一个商品标题会在数千个关键词下、
连续 365 天被重复存储；同一个关键词也会重复 365 次。把它们提取为维度后，事实表
单行从约 650 字节降到约 140 字节，12 个月 3 站的总体积从约 750 GB 降到约 200 GB。

另一个同等重要的收益：关键词子串搜索的 pg_trgm GIN 索引建在这里的约 1500 万行上，
而不是事实表的 8 亿行上，索引体积从约 60 GB 降到 1 GB 以内。

schema 由 ``database.Base`` 的 metadata 统一提供（取自 ``settings.DATABASE_SCHEMA``），
因此各模型不再单独硬编码 schema。

**站点没有维度表。** 站点的中文名、时区、域名都是 ``app.site_registry.SITE_CATALOG``
里的编译期常量，界面经 ``GET /api/upload/sites`` 直接取用。曾有一张 ``sites`` 参考表
存同样三个字段，从建表到删除期间没有任何代码写过它，库里始终是空的——而空表的害处不
只是没用：它是站点码第二份事实来源的入口，正是 ``GB``/``UK`` 两种写法这类问题的来源。
等站点真需要存运营可配的数据（站点级导入开关、汇率之类）时再建表也不迟，那时它才有
非展示用途的字段可存。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.table.analysis.column_comments import SITE_COLUMN_COMMENT
from database import Base

# 搜索索引统一用 pg_trgm 三元组 GIN，因为界面的搜索是子串 ILIKE '%x%'（沿用 V1 行为），
# 而非全文检索。tsvector GIN 对子串匹配无效——这是一个容易搞错的坑。
# pg_trgm 扩展由建表迁移一次性 CREATE EXTENSION。
_TRGM = "gin_trgm_ops"


class DimKeyword(Base):
    """关键词维度。

    关键词按站点隔离：同一个字符串在 US 与 DE 是两条独立记录，因为它们代表不同市场
    的搜索行为，其排名历史也各自独立。
    """

    __tablename__ = "dim_keyword"

    keyword_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    site: Mapped[str] = mapped_column(String(8), nullable=False, comment=SITE_COLUMN_COMMENT)
    keyword: Mapped[str] = mapped_column(String(500), nullable=False, comment="搜索关键词原文")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("site", "keyword", name="uq_dim_keyword_site_keyword"),
        # 关键词子串搜索（keyword ILIKE '%x%'）。trigram 索引对大小写不敏感，
        # 天然匹配 ILIKE 的语义。建在这张维度表（约 500 万行/站）上，而不是事实表
        # （约 8 亿行）——同样的 trigram 建在事实表上会是几十 GB 且拖慢热写路径。
        Index("idx_dim_keyword_trgm", "keyword", postgresql_using="gin",
              postgresql_ops={"keyword": _TRGM}),
        {"comment": "关键词维度：按站点隔离，(site,keyword) 唯一。子串搜索的 pg_trgm 索引"
                    "建在此表（约 1500 万行）而非事实表（约 8 亿行），是星型模型最大的一处节省。"},
    )


class DimProduct(Base):
    """商品维度（ASIN → 标题）。

    **只有标题是真正的商品属性。** 品牌与类目曾经也存在这里，那是把「点击量第 N 的
    品牌」与「点击量第 N 的商品」硬配的产物：实测同一 ASIN 固定在槽位 1 时，title 的
    冲突率是 0%，brand 11.7%、category 3.4%——后两者的冲突正是"它们不是商品属性"的
    直接证据。它们已移到 :class:`DimBrand` / :class:`DimCategory`，按名次挂在事实表上。

    采用 SCD Type 1：标题变化时原地更新，不保留历史版本。这意味着历史事实行会关联到
    商品的最新标题，无法还原当时的展示内容。当前分析场景可接受，但归档数据用于 AI
    训练时必须把维度值物化进导出文件，否则语料会失真。
    """

    __tablename__ = "dim_product"

    product_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asin: Mapped[str] = mapped_column(String(16), nullable=False, comment="亚马逊商品编号，10 位")
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("asin", name="uq_dim_product_asin"),
        # 界面对 title / asin 做子串 ILIKE 搜索（沿用 V1）。落在这张几百万行的维度表上，
        # 事实表不背任何搜索索引。
        Index("idx_dim_product_title_trgm", "title", postgresql_using="gin",
              postgresql_ops={"title": _TRGM}),
        Index("idx_dim_product_asin_trgm", "asin", postgresql_using="gin",
              postgresql_ops={"asin": _TRGM}),
        {"comment": "商品维度（ASIN → 标题）。SCD Type 1：变化时原地覆盖，不留历史版本。"},
    )


class DimBrand(Base):
    """品牌维度。

    品牌是**关键词级**的排行项，不是商品属性——源报表的「点击量第 1/2/3 的品牌」回答的是
    「搜这个词的人点击集中在哪几个品牌」，与「点击量第 N 的商品是谁」是两份独立的榜单。
    名次记在事实表的列位置上，这里只存名称。

    基数约 13.5 万/站/天，故主键用 ``integer``。
    """

    __tablename__ = "dim_brand"

    brand_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("name", name="uq_dim_brand_name"),
        {"comment": "品牌维度（关键词级排行项，非商品属性）。"},
    )


class DimCategory(Base):
    """类目维度。

    与品牌同理，是关键词级的排行项。基数仅 104，故主键用 ``smallint``——事实表上类目占
    3 列 × 2 亿行，用 integer 会白白多占一半空间。
    """

    __tablename__ = "dim_category"

    category_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("name", name="uq_dim_category_name"),
        {"comment": "类目维度（关键词级排行项，非商品属性）。"},
    )
