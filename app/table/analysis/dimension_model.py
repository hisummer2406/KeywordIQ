"""星型模型的维度表。

拆出维度表的原因是原始数据里存在两类巨量重复：同一个商品标题会在数千个关键词下、
连续 365 天被重复存储；同一个关键词也会重复 365 次。把它们提取为维度后，事实表
单行从约 650 字节降到约 140 字节，12 个月 3 站的总体积从约 750 GB 降到约 200 GB。

另一个同等重要的收益：关键词子串搜索的 pg_trgm GIN 索引建在这里的约 1500 万行上，
而不是事实表的 8 亿行上，索引体积从约 60 GB 降到 1 GB 以内。

schema 由 ``database.Base`` 的 metadata 统一提供（取自 ``settings.DATABASE_SCHEMA``），
因此各模型不再单独硬编码 schema。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

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
    site: Mapped[str] = mapped_column(String(8), nullable=False, comment="ISO alpha-2 站点码")
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
                    "建在此表（约 1500 万行）而非事实表（约 8 亿行）。"},
    )


class DimProduct(Base):
    """商品维度（ASIN → 标题、品牌、类目）。

    采用 SCD Type 1：商品信息变化时原地更新，不保留历史版本。这意味着历史事实行会
    关联到商品的最新标题与类目，无法还原当时的展示内容。当前分析场景可接受，但归档
    数据用于 AI 训练时必须把维度值物化进导出文件，否则语料会失真。
    """

    __tablename__ = "dim_product"

    product_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asin: Mapped[str] = mapped_column(String(16), nullable=False, comment="亚马逊商品编号，10 位")
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    brand: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    category: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("asin", name="uq_dim_product_asin"),
        # 界面对 title / brand / category / asin 四个字段都做子串 ILIKE 搜索（沿用 V1）。
        # 全部落在这张几百万行的维度表上，事实表不背任何搜索索引。
        # brand / category 另有精确匹配的下拉（走物化视图），与这里的子串搜索是两条路径。
        Index("idx_dim_product_title_trgm", "title", postgresql_using="gin",
              postgresql_ops={"title": _TRGM}),
        Index("idx_dim_product_brand_trgm", "brand", postgresql_using="gin",
              postgresql_ops={"brand": _TRGM}),
        Index("idx_dim_product_category_trgm", "category", postgresql_using="gin",
              postgresql_ops={"category": _TRGM}),
        Index("idx_dim_product_asin_trgm", "asin", postgresql_using="gin",
              postgresql_ops={"asin": _TRGM}),
        {"comment": "商品维度（ASIN → 标题/品牌/类目）。SCD Type 1：变化时原地覆盖，不留历史版本。"},
    )


class SiteRef(Base):
    """站点参考表，供界面展示站点中文名、时区与域名。

    它**不是**站点合法性的判定依据——那是 ``app.site_registry`` 的职责。这张表刻意
    不与事实表建外键：批量灌入时每行一次外键检查是纯粹的导入税，而导入是唯一的写入
    路径且已在应用层校验站点码。作为补偿，应有一条巡检查询定期比对库中实际出现的
    站点与站点清单。
    """

    __tablename__ = "sites"

    __table_args__ = {
        "comment": "站点参考表：仅供界面展示中文名/时区/域名。站点码合法性由 site_registry 保证，"
                   "此表不与事实表建外键。",
    }

    code: Mapped[str] = mapped_column(String(8), primary_key=True, comment="ISO alpha-2 站点码")
    name_zh: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    domain: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
