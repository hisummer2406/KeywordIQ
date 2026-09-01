"""竞对卖家在售 ASIN 与上报记录：共享事实层。

两张**普通表**，MUST NOT 登记进 ``app.table.analysis.partitioning.PARTITIONED_TABLES``。
登记即被保留清理按 ``HOT_WINDOW_MONTHS``（默认 3 个月）整分区 DROP，而
``first_seen_at`` 正是新发现判定的依据，越久越有价值——那个三个月窗口是为八亿行的 ABA
事实表定的。

**数据来自采集端主动上报（推模型），不是后端拉取。** 上报只表达「在某个时刻，上报者的
浏览器里见到了这些 ASIN 属于这个卖家」，**不承诺目录完整性**：运营点开 ``me=`` 页面通常
只看第一屏，翻到第几页取决于他当时在找什么。由此推出三条边界：

1. **不做下架判定**——"这次没见到"与"已下架"在数据上无法区分。独立站有三道完整性判据
   （每页 200 + 空页终止 + 不少于 ``/meta.json`` 声称的商品数）才敢判下架，被动采集一道
   都没有，故本模块没有 ``delisted_at``，查询侧也不做差集。
2. **不存逐次目录快照**——逐次快照记录的是「运营那天翻了几页」，而它与「这家店只有 18 个
   品」长得一模一样，没有任何地方会报错。改为一次上报一行观测元数据
   （``CompetitorAsinReport``），回答的是另一个问题：这个卖家的数据有多新、是怎么来的。
3. **产出的是「新发现」而不是「上新」**——见到得晚不等于上得晚。界面措辞一律用「发现于」
   与「近 7 日新发现」，0 只表示「已采集范围内没有新发现」，MUST NOT 读作「这家店没上新」。
   独立站那边有数据源直接给的 ``published_at``，才配称「上架于」。

粒度是 ``(seller_id, site, asin)`` 而**不是按 ASIN 全局唯一**：跟卖是 Amazon 的常态，
「A 店首次出现这个品」与「B 店首次出现这个品」是两件独立的事，各有各的
``first_seen_at``。按 ASIN 唯一存一行，第二家店的新发现会被第一家的记录吃掉，表现为上新流里
缺条目，而缺的恰是"跟卖"这种最该被看见的动作。

**时间的权威只有服务端。** ``first_seen_at`` 与 ``competitor_seller.baseline_at`` 一律取
服务端事务时间；上报体里的 ``captured_at`` 只落进 ``CompetitorAsinReport.captured_at``
供审计，**MUST NOT 参与任何判定**。让客户端时钟能左右新发现判定，等于一台时间偏了的机器
就能把整批老品推进上新流——而这个错误没有任何地方会报错。

schema 由 ``database.Base`` 的 metadata 统一提供（取自 ``settings.DATABASE_SCHEMA``）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.table.analysis.column_comments import SITE_COLUMN_COMMENT
from config import settings
from database import Base

_SCHEMA = settings.DATABASE_SCHEMA

ASIN_COMMENT = "亚马逊商品标识，形如 B0XXXXXXXX；与 dim_product.asin 同源，供后续联查"


class CompetitorAsinReport(Base):
    """一次上报的观测元数据：谁、何时、哪个卖家、多少条、在什么账号设置下采的。

    它**不是快照**——不存本次见到的 ASIN 清单（见模块 docstring）。它回答的是"这个卖家的
    数据有多新、是怎么来的"，在覆盖率取决于人的浏览习惯时，这是界面必须能说清的事：
    「很久没采过」与「确实没有新发现」在上新流里长得一模一样。

    ``request_id`` 是**客户端生成的幂等键**，唯一性按 ``(user_id, request_id)`` 判定。
    没有它，"上报幂等"只成立一半：``ON CONFLICT DO NOTHING`` 保证商品关系不重复，但同一个
    请求因网络超时被重发时仍会多出一行上报记录，把界面的「最近采集于」往前推——数据看起来
    比实际更新。唯一性按用户分域而非全局，是因为跨用户撞键会让 B 的上报被当成重复吞掉、
    并把 A 那次上报的计数回放给 B（既丢数据又泄露别人的记录）。

    ``request_hash`` 是**同一把钥匙配的第二道锁**：光有幂等键只能认出"这个键来过"，认不出
    "这两次是不是同一次上报"。插件若因 bug 复用了同一个 ``request_id`` 去报另一个卖家或
    另一批 ASIN，只按键判定就会回放旧记录并返回 200——新的观测被静默丢弃，甚至把另一个
    卖家的旧计数返回给调用方。与跨用户撞键是同一类失败，同样不会报错。所以命中键之后还要
    比对摘要：**一致才回放，不一致返 409**（``competitor.idempotency_key_reused``），
    绝不吞掉新请求。

    **摘要只覆盖决定"写什么"的字段**：``seller_id`` + ``site`` + 规范化去重并排序后的 ASIN
    集合，``request_id`` 本身不进摘要。审计与展示字段（``captured_at`` /
    ``ship_to_region`` / ``page_context`` / 标题 / 图片）**有意排除**——真正的重发送的是
    同一个请求体，把它们算进去不增加任何保护；反倒是重新读一遍 DOM 后重试的场景里，标题被
    Amazon 截得不一样、``captured_at`` 重算过，会撞出一个**假的 409**，而那恰恰是幂等键要
    保护的那次重试。

    ``ship_to_region`` 存下来但**不参与任何判定**。实测 Amazon 店铺搜索结果按操作者账号的
    收货地址过滤（2026-08-31 抽样 120 家，日本地址下 92 家显示无货，抽查 30 家却有 16 家
    仍在正常卖货），因此在非目标市场地址下"没见到"是账号设置的产物而非事实。留存它是为了
    出问题时能分辨"这批数据是在什么账号设置下采的"——这个信息事后补不回来，与独立站那边
    "币种事后补不回来"是同一类。

    写入顺序**必须是先本表、再 ``CompetitorSellerAsin``**：后者的 ``first_report_id``
    指向本表，且 ``new_count`` 要等商品行写完（``RETURNING`` 出真正新增的条目）才知道，
    因此本表在同一事务内先插入、后回填 ``new_count``。
    """

    __tablename__ = "competitor_asin_report"

    report_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey(f"{_SCHEMA}.user_center.id", ondelete="CASCADE"),
        nullable=False,
        comment="→ user_center.id，上报者；写共享层的授权判据落在他的关注关系上",
    )
    request_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="客户端生成的幂等键；按 (user_id, request_id) 唯一，重发命中即回放原记录不新增",
    )
    request_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment=(
            "请求摘要（sha256 十六进制），只覆盖 seller_id + site + 规范化去重排序后的 ASIN 集合。"
            "命中幂等键后比对：一致才回放，不一致返 409——防止同键被复用于另一次上报而静默吞掉"
        ),
    )
    seller_id: Mapped[str] = mapped_column(
        String(24), nullable=False, comment="→ competitor_seller.seller_id"
    )
    site: Mapped[str] = mapped_column(String(8), nullable=False, comment=SITE_COLUMN_COMMENT)
    reported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="服务端受理时刻；界面的「最近采集于」取本列的最大值",
    )
    captured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=(
            "采集端声称的采集时刻。**只作审计、MUST NOT 参与任何判定**："
            "客户端时钟不可信，新发现判定一律用服务端时间"
        ),
    )
    item_count: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="本次上报的条目数；MUST NOT 读作该卖家的在售商品总数"
    )
    new_count: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="本次新入库的条目数（重复上报时为 0）"
    )
    ship_to_region: Mapped[str | None] = mapped_column(
        String(8),
        nullable=True,
        comment="采集时账号的配送地区码。**只留档、不参与过滤或存活判定**，见类 docstring",
    )
    page_context: Mapped[str | None] = mapped_column(
        String(200), nullable=True, comment="采集时的页面上下文（排序方式、页码等），供排查用"
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["seller_id", "site"],
            [f"{_SCHEMA}.competitor_seller.seller_id", f"{_SCHEMA}.competitor_seller.site"],
            name="fk_competitor_asin_report_seller",
            ondelete="CASCADE",
        ),
        UniqueConstraint("user_id", "request_id", name="uq_competitor_asin_report_request"),
        # 「最近采集于」：按 (卖家, 站点) 取最新一行。
        Index("idx_competitor_asin_report_latest", "seller_id", "site", "reported_at"),
        {
            "comment": (
                "竞对 ASIN 上报的观测元数据：一次上报一行，不含商品清单；"
                "回答「数据有多新、怎么来的」，不是目录快照。"
            )
        },
    )


class CompetitorSellerAsin(Base):
    """某卖家在售的一个 ASIN：只增不改。

    ``first_seen_at`` **只在首次写入时落定，重复上报绝不更新**。运营每刷新一次页面插件就会
    再报一次，若每次都更新它，一个老品会因为被重新看到而跳进"今天的新发现"。同理
    ``title`` / ``image_url`` 也只在首次落定——它们是"见到时它长什么样"，不是需要跟踪的
    时序数据。

    价格、BSR、评价数**有意不存**：它们随时间变化，要存就得像独立站价格那样单开一张仅在
    变化时追加的表，而目前没有任何消费方（上新流主列表只放两类来源的交集字段）。
    """

    __tablename__ = "competitor_seller_asin"

    seller_id: Mapped[str] = mapped_column(
        String(24), primary_key=True, comment="→ competitor_seller.seller_id"
    )
    site: Mapped[str] = mapped_column(String(8), primary_key=True, comment=SITE_COLUMN_COMMENT)
    asin: Mapped[str] = mapped_column(String(16), primary_key=True, comment=ASIN_COMMENT)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment=(
            "该卖家名下首次见到这个 ASIN 的时刻，取服务端事务时间。**上新流的判据**："
            "first_seen_at > competitor_seller.baseline_at 才算新发现；重复上报不更新它"
        ),
    )
    first_report_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{_SCHEMA}.competitor_asin_report.report_id", ondelete="SET NULL"),
        nullable=True,
        comment=(
            "首次引入这一行的上报记录；共享层进了坏数据时凭它定位来源。"
            "上报者账号被删时置空而非连带删除——商品行是别人也在用的共享数据"
        ),
    )
    title: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="首次见到时的商品标题，只增不改；上报未给则为 NULL"
    )
    image_url: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="首次见到时的主图地址，只增不改"
    )

    __table_args__ = (
        PrimaryKeyConstraint("seller_id", "site", "asin", name="pk_competitor_seller_asin"),
        ForeignKeyConstraint(
            ["seller_id", "site"],
            [f"{_SCHEMA}.competitor_seller.seller_id", f"{_SCHEMA}.competitor_seller.site"],
            name="fk_competitor_seller_asin_seller",
            ondelete="CASCADE",
        ),
        # 上新流：按 (卖家, 站点) 取、按 first_seen_at 过滤时间窗并倒序取前若干条。
        Index("idx_competitor_seller_asin_new", "seller_id", "site", "first_seen_at"),
        {
            "comment": (
                "竞对卖家在售 ASIN：共享层、只增不改；粒度按卖家而非按 ASIN 全局唯一，"
                "跟卖时两家各有一行。不做下架判定。"
            )
        },
    )
