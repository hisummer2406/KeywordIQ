"""独立站监控：站点主体 + 商品 + 价格变更日志 + 畅销名次日快照 + 私有关注关系。

**五张普通表，MUST NOT 登记进 ``app.table.analysis.partitioning.PARTITIONED_TABLES``。**
登记即被保留清理按 ``HOT_WINDOW_MONTHS``（默认 3 个月）整分区 DROP，而价格与名次的历史
越长越值钱——「这个品半年来降过三次价」正是要看的东西。那个三个月窗口是为八亿行的 ABA
事实表设定的，与本模块的量级（估算约 110 万行/年）无关。

**三类数据分三张表，判据是粒度与写入模式两两都不同**（见
``openspec/changes/add-competitor-dtc-monitoring/design.md`` 决策 2）：

===========  ==========  ==================================
数据          粒度        写入模式
===========  ==========  ==================================
商品主体      商品         只增不改
价格          **变体**     **仅在与该变体上一条观测不同时追加**
销量名次      商品 × 日    每日追加，只存前 N 名
===========  ==========  ==================================

实测单店平均 6.3 个变体/商品（最大 169）。价格若按每日全量快照存，17 家店是 3360 万
行/年、100 家店是 1.98 亿行/年，而其中约 99% 与前一日完全相同；变更日志是约 100 万行/年。
这不只是空间——``dim_product`` 那次 ``IS DISTINCT FROM`` 的教训是**写入代价**：
PostgreSQL 的 UPDATE 无论新旧值是否相同都写新元组、标死旧元组、并在每个索引上插新条目，
同一份日报因此从 54 秒退化到 416 秒（issue #33）。

与 Amazon 卖家侧（``seller_model`` / ``watch_model``）**刻意分开建模**：
``CompetitorSeller`` 是 ``(seller_id, site)`` 复合主键，独立站两样都没有，而为它编造
一个站点码会给 ``app/site_registry.py`` 这个唯一事实来源开出第二个入口。但**分组共用**
``UserSellerGroup``——它本来就是站点无关的标签。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from config import settings
from database import Base

_SCHEMA = settings.DATABASE_SCHEMA

DOMAIN_COMMENT = "规范化后的站点域名（去协议/www/端口/路径），如 leteimall.com"


class CompetitorStore(Base):
    """独立站主体：全局共享，不携带账号属性——一个站点被十个用户关注也只抓一次。

    ``currency`` **不是可有可无的字段**。实测 17 个站点里已出现两种币种
    （leteimall/kydschoice 是 USD，blessrich 是 GBP），而 ``/products.json`` 的价格数字
    本身不带币种。不存它就会让 ``£12.99`` 与 ``$18.98`` 在界面上并排显示成同一个数；
    更要紧的是价格历史一旦入库，**事后补币种补不回来**——不知道当时那个数是哪国钱。

    抓取状态用状态列而非进程内计时器：``last_fetch_at`` 决定「到点了没」，进程重启、
    多 worker 或崩溃后都不失效。失效的表现是「再也不更新了」，与 issue #56 里那个
    「批次永远排队」是同一种病。
    """

    __tablename__ = "competitor_store"

    domain: Mapped[str] = mapped_column(String(255), primary_key=True, comment=DOMAIN_COMMENT)
    platform: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="SHOPIFY",
        comment="建站平台。当前只实现 SHOPIFY，非 Shopify 站点在加入时即被拒绝",
    )
    currency: Mapped[str | None] = mapped_column(
        String(8),
        nullable=True,
        comment="ISO 币种码，取自 /meta.json。价格数字本身不带币种，缺它则跨店比价无意义",
    )
    country: Mapped[str | None] = mapped_column(
        String(8), nullable=True, comment="商户所在国家码，取自 /meta.json"
    )
    shop_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="Shopify 店铺 id，取自 /meta.json"
    )
    myshopify_domain: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="稳定身份：商户换自定义域名时它不变，可据此发现「同一家店换了域名」",
    )
    ships_to_countries: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(8)), nullable=True, comment="可配送国家码列表，取自 /meta.json"
    )

    products_json_ok: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
        comment="/products.json 是否可用。false = 商户关闭了端点，是其选择而非故障，须与抓取失败分开展示",
    )
    last_fetch_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="上次拉取时刻。「到点了没」的唯一判据，NULL 表示从未拉取过",
    )
    catalog_complete: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
        comment="上次目录拉取是否完整。**下架判定的唯一前提**，不完整时只做并集写入、绝不判下架",
    )
    rank_ok: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
        comment="上次畅销榜解析是否成功。与 catalog_complete 相互独立，一方失败不阻止另一方写入",
    )
    last_fetch_status: Mapped[str | None] = mapped_column(
        String(24), nullable=True, comment="上次拉取结果：OK / INCOMPLETE / FAILED / NO_CATALOG_API"
    )
    last_error: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="上次失败的可排查信息，面向运维不面向终端用户"
    )
    consecutive_failures: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
        comment="连续失败次数，界面据此提示「连续 N 次失败」而不是只说最近一次",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # 认领到期站点：WHERE last_fetch_at IS NULL OR last_fetch_at < 阈值 ORDER BY 同列
        Index("idx_competitor_store_due", "last_fetch_at"),
        {"comment": "独立站主体：全局共享，不含账号属性；关注关系见 user_store_watch。"},
    )


class CompetitorStoreProduct(Base):
    """商品主体：只增不改。

    **三个时间都要存，而新品判定一个都不看。** 判据是「这个 ``product_id`` 此前是否已在
    库里」——商户批量重新发布会让 ``published_at`` 整体跳到当日，据此判定会伪造出一批
    假上新。三个时间保留是为了出问题时能分辨：``published_at``/``source_created_at``
    是数据源说的，``first_seen_at`` 是我们说的。

    由此得到 Amazon 侧没有的好处：**首刷即有历史**——第一次拉取把 ``published_at`` 原样
    写入，当场就能回答「这家店近 7 天上了什么」，一条基线都不用建。但首刷那批
    **不进上新流**（上新流按 ``first_seen_at``），因为新纳入站点上个月发的商品不是
    「今天上新」，只是我们刚开始看。这正是两个时间都要存的第二个理由。
    """

    __tablename__ = "competitor_store_product"

    domain: Mapped[str] = mapped_column(String(255), primary_key=True, comment=DOMAIN_COMMENT)
    product_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, comment="Shopify 商品 id，站点内唯一"
    )
    handle: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="商品 URL 短名，畅销榜按它匹配名次"
    )
    title: Mapped[str] = mapped_column(Text, nullable=False, server_default="", comment="商品标题")
    vendor: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="供应商/品牌名")
    product_type: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="商户自定义的商品品类"
    )
    tags: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True, comment="商户标签。展示用，无下游按标签的连接查询，故不建关联表"
    )
    image_url: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="主图地址。只存地址不镜像文件——目录端点是公开的，不必存副本"
    )

    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="数据源给的上架时间。**新品判定不看它**（批量重新发布会让它整体跳到当日）",
    )
    source_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="数据源给的创建时间，用于与 published_at 交叉分辨"
    )
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="数据源给的最后更新时间"
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="我们首次见到该商品的时刻。**上新流按这一列**，首刷那批不进流",
    )
    # 这里**没有** last_seen_at，是有意的：它需要每次拉取更新每一行（100 家店时约
    # 8 万行/天的无条件 UPDATE），与本表「只增不改」直接冲突，而它是完全可推导的——
    # delisted_at 为空即「上次完整拉取时它还在」，那个时刻就是 competitor_store.last_fetch_at。
    delisted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="判定下架的时刻。**仅在整次目录拉取完整成功时才写**，不完整时绝不判下架",
    )

    __table_args__ = (
        PrimaryKeyConstraint("domain", "product_id", name="pk_competitor_store_product"),
        ForeignKeyConstraint(
            ["domain"],
            [f"{_SCHEMA}.competitor_store.domain"],
            name="fk_competitor_store_product_store",
            ondelete="CASCADE",
        ),
        # 「近 7 日上新数」与上新流都按 (站点, 首次见到时间) 取。
        Index("idx_competitor_store_product_new", "domain", "first_seen_at"),
        # 畅销名次按 handle 回连商品。
        Index("idx_competitor_store_product_handle", "domain", "handle"),
        {"comment": "独立站商品主体：只增不改；新品判定看商品是否已在库，不看 published_at。"},
    )


class CompetitorStorePrice(Base):
    """价格变更日志：**只在与该变体上一条观测不同时才追加一行**。

    MUST NOT 每次拉取都无条件写入。粒度是**变体**不是商品——``price`` 是变体属性，
    实测单店平均 6.3 个变体/商品（最大 169）。

    没有「当前价」这一列：当前价就是该变体 ``observed_at`` 最大的那一行。多存一份当前价
    等于给同一个事实开两个来源，而两者迟早分叉。
    """

    __tablename__ = "competitor_store_price"

    domain: Mapped[str] = mapped_column(String(255), primary_key=True, comment=DOMAIN_COMMENT)
    variant_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, comment="Shopify 变体 id，站点内唯一"
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, comment="观测到这组取值的时刻"
    )
    product_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="→ competitor_store_product.product_id"
    )
    sku: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="商户自填的 SKU")
    price: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
        comment="售价。币种见 competitor_store.currency。**缺失存 NULL 不存 0**——0 会被当成免费，静默产生假降价",
    )
    compare_at_price: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True, comment="划线原价，可空"
    )
    available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", comment="是否可购买"
    )

    __table_args__ = (
        PrimaryKeyConstraint(
            "domain", "variant_id", "observed_at", name="pk_competitor_store_price"
        ),
        ForeignKeyConstraint(
            ["domain"],
            [f"{_SCHEMA}.competitor_store.domain"],
            name="fk_competitor_store_price_store",
            ondelete="CASCADE",
        ),
        # 「这个商品的调价历史」按 (站点, 商品) 取，主键起不到这个作用。
        Index("idx_competitor_store_price_product", "domain", "product_id", "observed_at"),
        {"comment": "独立站价格变更日志：仅在取值变化时追加，不做每日全量快照（约 30 倍写放大）。"},
    )


class CompetitorStoreRank(Base):
    """畅销名次日快照：只存前 N 名（``DTC_RANK_TOP_N``）。

    数据源只提供店内相对排序，**没有任何绝对销量**。因此这里存的是名次，界面上
    MUST NOT 换算或呈现为销量数字、销售额，也不可跨店比较。

    只存前 N 名是有意的：第 300 名与第 800 名的差别没有信息量，但存全量会让这张表膨胀
    5~10 倍。名次的价值在**变化**——某商品两周内从第 400 名升到第 12 名。

    ``captured_on`` 是日期而非时间戳，因此同一天重复拉取即覆盖，天然幂等。
    """

    __tablename__ = "competitor_store_rank"

    domain: Mapped[str] = mapped_column(String(255), primary_key=True, comment=DOMAIN_COMMENT)
    captured_on: Mapped[date] = mapped_column(
        Date, primary_key=True, comment="快照日期。用日期不用时间戳，同一天重拉即覆盖"
    )
    rank: Mapped[int] = mapped_column(
        Integer, primary_key=True, comment="店内畅销名次，1 起。**相对名次，不是销量**"
    )
    product_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="→ competitor_store_product.product_id"
    )

    __table_args__ = (
        PrimaryKeyConstraint("domain", "captured_on", "rank", name="pk_competitor_store_rank"),
        ForeignKeyConstraint(
            ["domain"],
            [f"{_SCHEMA}.competitor_store.domain"],
            name="fk_competitor_store_rank_store",
            ondelete="CASCADE",
        ),
        # 「某商品的名次走势」按 (站点, 商品) 取，主键按名次排列起不到这个作用。
        Index("idx_competitor_store_rank_product", "domain", "product_id", "captured_on"),
        {"comment": "独立站畅销名次日快照：只存前 N 名；相对名次，数据源不提供绝对销量。"},
    )


class UserStoreWatch(Base):
    """独立站关注关系：账号私有。

    与 ``UserSellerWatch`` 是两张表而非一张——键不同（域名 vs ``(seller_id, site)``）。
    但 ``group_id`` 指向**同一张** ``user_seller_group``：分组是纯标签，MUST NOT 因成员
    类型分裂成两套分组体系。运营的心智是「我在盯这一批竞对」，不是「我在盯这一批亚马逊
    卖家」外加「我在盯这一批独立站」。

    删组不取消关注（``ON DELETE SET NULL``），与 Amazon 侧同一条纪律。
    """

    __tablename__ = "user_store_watch"

    watch_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey(f"{_SCHEMA}.user_center.id", ondelete="CASCADE"),
        nullable=False,
        comment="→ user_center.id，关注关系的唯一归属人",
    )
    domain: Mapped[str] = mapped_column(
        String(255),
        ForeignKey(f"{_SCHEMA}.competitor_store.domain", ondelete="CASCADE"),
        nullable=False,
        comment="→ competitor_store.domain",
    )
    group_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(f"{_SCHEMA}.user_seller_group.group_id", ondelete="SET NULL"),
        nullable=True,
        comment="→ user_seller_group.group_id（与 Amazon 卖家关注共用同一张分组表），NULL 表示未分组",
    )
    note: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="用户备注")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "domain", name="uq_user_store_watch_user_domain"),
        # 主表格按 (用户, 分组) 取，与 user_seller_watch 同一查询路径。
        Index("idx_user_store_watch_owner", "user_id", "group_id"),
        {"comment": "独立站关注关系：账号私有，UNIQUE(user_id, domain)；分组与卖家关注共用。"},
    )
