"""星型模型的事实表与统计表。

两张事实表结构一致，只在时间粒度上不同：``fact_daily`` 每天一期约 30 万行/站，
``fact_weekly`` 每周一期约 300 万行/站。

三条贯穿始终的设计约束：

1. **写入后不可变。** 排名、上期排名、排名变化、是否新词都在写入时一次性算定，
   此后不再修改。因此表上不预留 ``fillfactor``、不需要为死元组规划 autovacuum，
   而且按月归档成立的前提正是"已归档的月份不会有回写需求"。
2. **不设冗余派生字段。** V1 的 ``ranking_trend_day`` JSONB 之所以存在，是因为当时
   没有历史行；时序化之后它是纯冗余，7 天走势由查询层从历史行计算。
3. **不建外键。** 8 亿行的批量灌入下，每行一次外键检查是纯粹的导入税。参照完整性
   由导入链路保证（维度先于事实写入）。

分区：一级按 ``report_date`` 做月度 RANGE 分区，二级按 ``site`` 做 LIST 分区。
子分区由 ``partitioning.ensure_fact_partitions`` 在导入前按需创建。PostgreSQL 要求
分区表的主键必须包含分区键，主键 ``(site, report_date, keyword_id)`` 天然满足。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.table.analysis.column_comments import (
    PERIOD_COLUMN_COMMENT,
    SITE_COLUMN_COMMENT,
)
from database import Base

# 份额类字段统一精度，与源报表一致。
_SHARE = Numeric(10, 2)


def _fact_indexes(prefix: str, date_column: str) -> tuple[Index, ...]:
    """事实表的索引集合。两张事实表用同一套，只有表名前缀与日期列名不同。

    这套索引由 V1 生产库的 ``pg_stat_user_indexes`` 真实使用数据定型（不是猜的）：

    - 日期+排名的复合索引是绝对热点（V1 ``idx_date_ranking_nonzero`` 被扫 8.2 万次），
      对应这里的 ``_ranking``。
    - 首次入榜筛选不在事实表上：它读 ``dim_keyword`` 的永久首次记录（见
      ``keyword-first-seen``），事实行不再带 ``is_new``，也就没有 ``_new`` 部分索引。
    - top1 商品关联（V1 ``idx_optimized_count`` 2736 次、``idx_top_category_with_id``
      1908 次），对应 ``_top1_product``。
    - 份额排序使用度中等（V1 click_share 542 次、conversion 173 次），保留但不再拆出
      任何"范围"部分索引——V1 的 ``idx_shares_range`` / ``*_share_range`` 三个部分索引
      **实测 0 次命中**，是投机建的废索引。V2 不重蹈覆辙。

    7 天走势索引在 V1 无对应（V1 用 JSONB 存趋势，无历史行），是 V2 的新查询模式。
    品牌索引同理，随品牌归位到关键词级而来。
    """
    return (
        # 7 天走势：按 (站点, 关键词) 定位，再对日期做范围扫描。
        # 注意列序与主键相反——主键是 (site, report_date, keyword_id)，服务不了这个查询。
        # 这是最容易漏掉的一个索引。
        Index(f"idx_{prefix}_keyword_history", "site", "keyword_id", date_column),
        # 默认排序：当期排名升序。V1 实测最热路径。
        Index(f"idx_{prefix}_ranking", "site", date_column, "current_ranking"),
        # 按排名变化排序/筛选。
        Index(f"idx_{prefix}_change", "site", date_column, "ranking_change"),
        # ASIN / 标题筛选：先在 dim_product 上解析出 product_id，再回查事实表。
        # top2/top3 同样要建——ASIN 精确匹配与"竞对新发现"筛选都按前三任一命中（与品牌/
        # 类目同一形状），只建 top1 会让另外两个槽位退化成全表扫描（issue #56 P2）。
        *(
            Index(f"idx_{prefix}_top{n}_product", "site", date_column, f"top{n}_product_id")
            for n in (1, 2, 3)
        ),
        # 份额排序（使用度中等，但不拆"范围"部分索引——V1 实测那些 0 命中）。
        Index(f"idx_{prefix}_click_share", "site", date_column, "top1_click_share"),
        Index(f"idx_{prefix}_conversion_share", "site", date_column, "top1_conversion_share"),
        # 品牌筛选「前三任一命中」= 三列 OR，计划器用 BitmapOr 合并三路，故三个独立
        # btree 而非一个复合索引。真实数据上实测选定（US 周报 278.5 万行：无索引
        # 499ms → 187ms，见变更 keyword-level-brand-category 的 design 决策 4）。
        #
        # 不用表达式 GIN 的决定性理由不是快那 67ms，而是**表达式 GIN 会把 SQL 形状焊死
        # 在索引上**：只有写成 ARRAY[...] && ARRAY[...] 才命中，_apply_filters 一旦改回
        # 自然的 IN ... OR ...，索引会静默失效退回全切片扫描，而测试不会报错。
        #
        # 列序 (site, 日期, 品牌)：前两列是作用域边界，每个查询都带；品牌在最后做等值。
        # **类目刻意不建索引**——基数只有 122，选择性太低，索引很可能不被计划器选中
        # （V1 有过投机建索引、实测 0 命中的先例），等有真实慢查询再说。
        *(
            Index(f"idx_{prefix}_top{n}_brand", "site", date_column, f"top{n}_brand_id")
            for n in (1, 2, 3)
        ),
    )


class FactDaily(Base):
    """日报事实表。约 30 万行/站/天。"""

    __tablename__ = "fact_daily"

    site: Mapped[str] = mapped_column(String(8), primary_key=True, comment=SITE_COLUMN_COMMENT)
    report_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="报告日期（一级 RANGE 分区键）")
    keyword_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment="→ dim_keyword")

    current_ranking: Mapped[int] = mapped_column(Integer, nullable=False, comment="当期搜索频率排名")
    previous_ranking: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="上一期排名，写入时算定")
    ranking_change: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="较上一期的变化，写入时算定")

    top1_product_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="点击量第 1 名商品 → dim_product.product_id（可空：无商品时）")
    top1_click_share: Mapped[float] = mapped_column(
        _SHARE, nullable=False, default=0, comment="第 1 名商品的点击份额（%）")
    top1_conversion_share: Mapped[float] = mapped_column(
        _SHARE, nullable=False, default=0, comment="第 1 名商品的转化份额（%）")

    top2_product_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="点击量第 2 名商品 → dim_product.product_id")
    top2_click_share: Mapped[float] = mapped_column(_SHARE, nullable=False, default=0)
    top2_conversion_share: Mapped[float] = mapped_column(_SHARE, nullable=False, default=0)

    top3_product_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="点击量第 3 名商品 → dim_product.product_id")
    top3_click_share: Mapped[float] = mapped_column(_SHARE, nullable=False, default=0)
    top3_conversion_share: Mapped[float] = mapped_column(_SHARE, nullable=False, default=0)

    # 关键词级的前三品牌与前三类目。**不是商品属性**——源报表里它们是与商品榜单彼此
    # 独立的两份排行，商品槽位本身不携带品牌与类目。名次由列位置承载。
    #
    # 全部可空且**无默认值**：带 DEFAULT 的 ADD COLUMN 会让 PostgreSQL 重写整张表，
    # 2 亿行的分区表上是灾难；无默认值时是纯元数据操作。可空也是历史分区的必然要求
    # ——那些数据没有这六个字段，只能靠按批次重导补上。
    top1_brand_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="→ dim_brand")
    top2_brand_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    top3_brand_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    top1_category_id: Mapped[int | None] = mapped_column(SmallInteger, nullable=True, comment="→ dim_category")
    top2_category_id: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    top3_category_id: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        *_fact_indexes("fact_daily", "report_date"),
        {"postgresql_partition_by": "RANGE (report_date)",
         "comment": "日报事实表（约 30 万行/站/天）。按 report_date 月度 RANGE 分区 + 按 site "
                    "二级 LIST 分区。写入后不可变，纯追加；派生字段在写入时算定。"},
    )


class FactWeekly(Base):
    """周报事实表。约 300 万行/站/周——是日报的 10 倍，两者必须按不同问题域对待。"""

    __tablename__ = "fact_weekly"

    site: Mapped[str] = mapped_column(String(8), primary_key=True, comment=SITE_COLUMN_COMMENT)
    report_date_week: Mapped[date] = mapped_column(
        Date, primary_key=True, comment="所属周的报告日期（一级 RANGE 分区键）")
    keyword_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment="→ dim_keyword")

    current_ranking: Mapped[int] = mapped_column(Integer, nullable=False, comment="当期搜索频率排名")
    previous_ranking: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="上一期排名，写入时算定")
    ranking_change: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="较上一期的变化，写入时算定")

    top1_product_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="点击量第 1 名商品 → dim_product.product_id（可空：无商品时）")
    top1_click_share: Mapped[float] = mapped_column(
        _SHARE, nullable=False, default=0, comment="第 1 名商品的点击份额（%）")
    top1_conversion_share: Mapped[float] = mapped_column(
        _SHARE, nullable=False, default=0, comment="第 1 名商品的转化份额（%）")

    top2_product_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="点击量第 2 名商品 → dim_product.product_id")
    top2_click_share: Mapped[float] = mapped_column(_SHARE, nullable=False, default=0)
    top2_conversion_share: Mapped[float] = mapped_column(_SHARE, nullable=False, default=0)

    top3_product_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="点击量第 3 名商品 → dim_product.product_id")
    top3_click_share: Mapped[float] = mapped_column(_SHARE, nullable=False, default=0)
    top3_conversion_share: Mapped[float] = mapped_column(_SHARE, nullable=False, default=0)

    # 关键词级的前三品牌与前三类目。**不是商品属性**——源报表里它们是与商品榜单彼此
    # 独立的两份排行，商品槽位本身不携带品牌与类目。名次由列位置承载。
    #
    # 全部可空且**无默认值**：带 DEFAULT 的 ADD COLUMN 会让 PostgreSQL 重写整张表，
    # 2 亿行的分区表上是灾难；无默认值时是纯元数据操作。可空也是历史分区的必然要求
    # ——那些数据没有这六个字段，只能靠按批次重导补上。
    top1_brand_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="→ dim_brand")
    top2_brand_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    top3_brand_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    top1_category_id: Mapped[int | None] = mapped_column(SmallInteger, nullable=True, comment="→ dim_category")
    top2_category_id: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    top3_category_id: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        *_fact_indexes("fact_weekly", "report_date_week"),
        {"postgresql_partition_by": "RANGE (report_date_week)",
         "comment": "周报事实表（约 300 万行/站/周）。结构与 fact_daily 一致，日期粒度为周。"
                    "与日报是独立数据域，各自拥有一套 Top 商品字段。"},
    )


class FactDropped(Base):
    """掉榜词：在行窗口内出现过、但在锚定期没有事实数据的关键词。

    行窗口按周期取——日报是锚定日往前 7 天，周报是锚定周往前 4 周。这张表回答的是
    「选这一期时，哪些词刚刚掉出榜单」，而那正是选品时最该看见、旧模型却完全看不到的信号
    （当期切片里它们根本不存在，只能靠人肉对比两天的导出文件）。

    三条与事实表一致的约束：

    1. **导入时算定，查询时只读。** 掉榜是锚定期的一项事实，锚定期一旦确定就不再变化，
       正好落在「写入时算定、只追加」的模式里。放到查询侧实时算的话，天视图要在 180 万行
       上做反连接、周视图 900 万行，而且**每翻一页重跑一次**。
    2. **只追加，不原地更新。** 唯一例外与事实表相同：同一期重复导入时先删该期再重算。
    3. **按锚定期月度分区**，随保留窗口的分区清理一同移出。不这么做它就成了一张只涨不掉
       的表——事实数据滚出去了，指向它们的掉榜记录还在。

    **与事实表不同的是不做站点二级分区**：掉榜集合是差集而非全量，比事实表小一到两个
    数量级，二级分区的裁剪收益这个量级上索引已经够用，代价却是分区数翻倍（站点数 × 月数）。
    站点因此是普通列，靠索引前缀裁剪。
    """

    __tablename__ = "fact_dropped"

    site: Mapped[str] = mapped_column(String(8), primary_key=True, comment=SITE_COLUMN_COMMENT)
    period_type: Mapped[str] = mapped_column(String(8), primary_key=True, comment=PERIOD_COLUMN_COMMENT)
    anchor_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="锚定期（分区键）")
    keyword_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment="→ dim_keyword")

    # 掉榜行唯一能携带的跨期信息。界面上这一行除了历史排名与趋势之外全靠这两个数，
    # 且必须明确标注期次——不标注就成了「同一列混着不同天的数字」。
    last_seen_date: Mapped[date] = mapped_column(Date, nullable=False, comment="窗口内最后一次在榜的期次")
    last_ranking: Mapped[int] = mapped_column(Integer, nullable=False, comment="最后一次在榜时的排名")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # 掉榜段的分页：作用域三元组做前缀、最后在榜排名承担排序。索引顺序即分页顺序，
        # 因此计划里不该出现排序节点——出现了说明列序被改动过。
        Index("idx_fact_dropped_segment", "site", "period_type", "anchor_date", "last_ranking"),
        # 单关键词历史查询（趋势详情）：主键 (site, period_type, anchor_date, keyword_id)
        # 一旦 anchor_date 变成范围（而非等值），keyword_id 就退化成扫描内 filter——
        # range=ALL 或 30 天窗口下，代价随该站点/视图窗口内的掉榜行数增长，而不是随目标
        # 关键词的历史行数增长，违背「只按单个 keyword_id 的历史索引取数，不扫全站榜单」
        # 的设计前提。keyword_id 提到 anchor_date 之前，把范围收窄到单个关键词后再扫描。
        Index("idx_fact_dropped_keyword_history", "site", "period_type", "keyword_id", "anchor_date"),
        {"postgresql_partition_by": "RANGE (anchor_date)",
         "comment": "掉榜词：行窗口内出现过但锚定期无数据的关键词。导入时算定、查询时只读；"
                    "按锚定期月度 RANGE 分区，随保留窗口一同移出；不做站点二级分区。"},
    )


class FactGrowth(Base):
    """持续增长词：近 7 天名次持续前进、且幅度达标的关键词。

    口径（三条同时满足，窗口是锚定日往前 7 期 ``[d-6, d]``）：

    1. 窗口内**每一期都在榜**——七期在 ``fact_daily`` 里都有行；
    2. **累计净增** ``rank(d-6) − rank(d)`` 超过 ``GROWTH_MIN_RANK_GAIN``（默认 1 万名）；
    3. 窗口内**每一个日环比** ``(前期排名 − 本期排名) / 前期排名`` 都 ≥
       ``GROWTH_MIN_DAILY_RATE``（默认 10%）。

    七期数据产生**六个**日环比，不是七个。要凑满七个环比就得把窗口扩到八期，那与掉榜
    的七天窗口对不齐，且多依赖一天的数据——需求方确认按六个环比判定。

    **只有日报有这个概念**，周报没有「连续 7 天」。因此这张表没有 ``period_type`` 列：
    有那一列就等于宣称 WEEK 也会出现在这里。

    与 :class:`FactDropped` 同属「导入时算定、查询时只读」：口径固定，实时算要在 210 万行
    （7 期 × 30 万）上做窗口聚合，而且**每翻一页重跑一次**；算定之后成本从「每次查询」
    降到「每期一次」。同样只追加不更新，同一期重复导入时先删该期再重算。

    **只存命中行**（每期数千行），不存全量指标。存下所有「连续七天在榜」的词及其指标能
    让改阈值免回填，但那是每站每期约 20 万行、三个月约 5400 万行——为一个以季度计频率的
    阈值调整长期背一张与事实表同量级的表。代价是**改阈值后旧期次仍是旧口径**，必须跑
    ``scripts/backfill_growth.py`` 重算。

    分区与掉榜表一致：按 ``anchor_date`` 月度 RANGE、**不做站点二级分区**（量级比事实表
    小两到三个数量级，索引前缀裁剪已经够用），随保留窗口的分区清理一同移出。
    """

    __tablename__ = "fact_growth"

    site: Mapped[str] = mapped_column(String(8), primary_key=True, comment=SITE_COLUMN_COMMENT)
    anchor_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="锚定日（分区键），窗口右端")
    keyword_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment="→ dim_keyword")

    # 起止排名都存，界面才能回答「从哪儿涨到哪儿」；只存净增的话，从 10 万涨到 6 万与
    # 从 5 万涨到 1 万在表里一模一样，而这两件事在选品上完全不是一回事。
    start_ranking: Mapped[int] = mapped_column(Integer, nullable=False, comment="窗口首期（d-6）排名")
    end_ranking: Mapped[int] = mapped_column(Integer, nullable=False, comment="锚定期（d）排名")
    rank_gain: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="累计净增名次 = start_ranking - end_ranking，正数为名次前进"
    )
    # 存最低环比而不只存「命中」这个布尔，界面才能区分「刚过线」与「远超线」。
    min_growth_rate: Mapped[float] = mapped_column(
        Numeric(6, 4), nullable=False, comment="窗口内 6 个日环比的最小值，如 0.1234 表示 12.34%"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # 榜单分页：作用域二元组做前缀、净增名次承担排序。
        # 不写 DESC——btree 可反向扫描，默认排序（净增降序）照样免排序节点，而表达式/
        # 带方向的索引会在 autogenerate 里产生比不掉的差异（集成测试要求 autogenerate 为空）。
        Index("idx_fact_growth_rank", "site", "anchor_date", "rank_gain"),
        {"postgresql_partition_by": "RANGE (anchor_date)",
         "comment": "持续增长词：近 7 期名次持续前进且幅度达标的关键词。仅日报；导入时算定、"
                    "查询时只读；按锚定日月度 RANGE 分区，随保留窗口一同移出；不做站点二级分区。"},
    )


class SiteDataStats(Base):
    """按站点 + 周期 + 期次的行数统计。

    承担两个用途，都是为了避免在 8 亿行上做实时聚合：

    1. **分页总数**——V1 用 ``pg_class.reltuples`` 估算全表行数，在多站点多日期下毫无
       意义。这里按作用域预先记好，查询侧 O(1) 读取。
    2. **日期选择器的可选日期**——"哪些日期有数据"直接来自这张表，不必扫事实表。

    由导入链路在每次导入结束时写入。
    """

    __tablename__ = "site_data_stats"

    site: Mapped[str] = mapped_column(String(8), primary_key=True, comment=SITE_COLUMN_COMMENT)
    period_type: Mapped[str] = mapped_column(String(8), primary_key=True, comment=PERIOD_COLUMN_COMMENT)
    report_date: Mapped[date] = mapped_column(Date, primary_key=True)
    row_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0,
        comment="在榜行数（该站点/周期/期次），供分页总数直接读取",
    )
    # 掉榜行数与在榜行数同粒度预存，分页总数才能保持 O(1)——实时数就是每翻一页付一次
    # 扫描代价，而避免这件事正是这张表当初存在的理由。
    dropped_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0"),
        comment="该期的掉榜词数（见 fact_dropped）",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # 日期选择器：按站点 + 周期取最新与全部可选日期。
        Index("idx_site_data_stats_lookup", "site", "period_type", "report_date"),
        {"comment": "按 (站点,周期,期次) 的行数统计，服务分页总数与日期选择器；导入结束时写入。"},
    )
