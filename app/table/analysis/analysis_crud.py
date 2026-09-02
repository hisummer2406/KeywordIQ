"""站点作用域查询的数据访问层（V2 星型模型）。

只读 fact_daily / fact_weekly（单站点单日期切片）+ dim_keyword / dim_product，
日期选择器与分页总数读 site_data_stats，类目下拉读 category_stats 物化视图。
无业务逻辑（作用域解析、默认站点等在 service 层）。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, NamedTuple

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session, aliased

from app.table.analysis.dimension_model import (
    DimBrand,
    DimCategory,
    DimKeyword,
    DimProduct,
)
from app.table.analysis.fact_model import (
    FactDaily,
    FactDropped,
    FactWeekly,
    SiteDataStats,
)
from app.table.competitor.asin_model import CompetitorSellerAsin
from app.table.competitor.seller_model import CompetitorSeller
from app.table.competitor.watch_crud import NEW_DISCOVERY_WINDOW_DAYS
from app.table.competitor.watch_model import UserSellerWatch
from app.table.search.search_schemas import AnalysisSearchRequest
from config import settings

_SCHEMA = settings.DATABASE_SCHEMA

# 列表总数**一律精确**，不封顶。
#
# 这里曾有一个 `FILTERED_COUNT_CAP = 10000`，是从 V1 沿用的权衡——V1 是单张大宽表，
# 一次精确计数真的要扫全表且逐月变慢，所以数到一万就停。**V2 这个前提不成立了**：
# 每个查询本来就被锁死在单一 (站点, 视图, 日期) 切片里，计数的代价由切片大小决定，
# 不随保留窗口增长。日报切片恒定约 30–40 万行，周报约 300 万行，三个月后还是这个数。
#
# 实测（US 生产量级数据，`Parallel Seq Scan` 单分区）：
#   日报 40 万行  类目筛选  热 168ms / 冷 677ms
#   周报 278 万行 类目筛选  热 242–375ms / 冷 2286ms
#
# 封顶省下的是这些，代价却是一个**无法与精确值区分**的 total：返回 10000 时，调用方
# 无从判断它是"恰好一万"还是"至少一万"。前端据此渲染翻页器，于是 9 万条命中里有 8 万条
# 在界面上根本够不着——数据在、接口也返回，就是没有那个页码按钮。
#
# 类目列上**没有**索引，且实测加了也没用：选择性 23% 时计划器直接弃用（`Parallel Seq Scan`
# 依旧），加索引前后的差异全部来自缓存冷热。不要再试一次。

# 导出时服务端游标每次取回的行数。块内驻留内存，故不宜大；日视图还要按块回查走势，
# 太小则回查次数过多。
EXPORT_CHUNK_SIZE = 2000

# 排序白名单（两视图字段名相同）。搜索趋势不在此列——它是附加信息、
# 非结果集来源字段，收到就忽略、回落默认。
SORTABLE_FIELDS = frozenset(
    {
        "current_ranking",
        "ranking_change",
        "previous_ranking",
        "top1_click_share",
        "top1_conversion_share",
        "is_new",
    }
)
_DEFAULT_ORDER = "current_ranking"


def fact_and_datecol(view: str):
    """按视图返回 (事实表模型, 日期列)。

    公开给词库盯盘用。**分区列名两表不同**（日报 ``report_date``、周报
    ``report_date_week``），照着日报的列名写周报查询会直接报 ``UndefinedColumn``，
    所以任何跨视图的查询都该从这里取列，而不是自己写列名。
    """
    if view == "WEEK":
        return FactWeekly, FactWeekly.report_date_week
    return FactDaily, FactDaily.report_date


def request_has_filters(req: AnalysisSearchRequest) -> bool:
    """请求是否带任何筛选（决定总数走 site_data_stats 还是上限计数）。

    **新增筛选字段必须加进这个元组。** 漏掉的表现是：该字段确实过滤了行，总数却仍报告
    "站点当期总词数"这个与筛选前一样的数字，分页页码全部对不上，且两条路径都能正常跑完、
    不会有任何报错或测试失败（`competitor_new` 的教训，见 issue #56 P2 design.md 决策 3）。

    **`competitor_new` 用 `or None` 而不是裸值**：与三态的 `is_new`（`None`/`True`/`False`
    都是各自不同的筛选结果）不同，`competitor_new` 只有两态——`_apply_filters` 里是
    `if req.competitor_new:`，`False` 与 `None` 的查询结果完全一样（都不施加条件）。
    裸传 `req.competitor_new` 会让 `False` 被 `v is not None` 判成"带筛选"，从而错误地
    切到精确计数路径（PR #66 复审 P2 指出）——这条路径本身没有性能问题，错的是语义：
    调用方传 `competitor_new=false` 时应当等价于没传这个字段。
    """
    return any(
        v is not None
        for v in (
            req.keyword, req.asin, req.product_title,
            req.brands or None, req.categories or None, req.ranking_min, req.ranking_max, req.change_min,
            req.change_max, req.click_share_min, req.click_share_max,
            req.conversion_share_min, req.conversion_share_max, req.is_new,
            req.competitor_new or None,
        )
    )


def available_dates_stmt(site: str, view: str):
    """该 (站点, 视图) 有数据且在保留窗口内的日期，倒序。来源 site_data_stats。"""
    return (
        select(SiteDataStats.report_date)
        .where(SiteDataStats.site == site, SiteDataStats.period_type == view)
        .order_by(SiteDataStats.report_date.desc())
    )


def scope_row_count_stmt(site: str, view: str, on: date):
    """作用域内无筛选总数：直接读 site_data_stats.row_count（O(1)）。"""
    return select(SiteDataStats.row_count).where(
        SiteDataStats.site == site,
        SiteDataStats.period_type == view,
        SiteDataStats.report_date == on,
    )


def scope_dropped_count_stmt(site: str, view: str, on: date):
    """作用域内的掉榜行数：读 site_data_stats.dropped_count（O(1)）。

    与 ``row_count`` 同粒度预存。分页把这两个数当作两段行集合的分界线，实时聚合出来的
    数与写入时刻的数不一致会让页与页之间错位。
    """
    return select(SiteDataStats.dropped_count).where(
        SiteDataStats.site == site,
        SiteDataStats.period_type == view,
        SiteDataStats.report_date == on,
    )


def dropped_stmt(site: str, view: str, on: date, offset: int, limit: int):
    """掉榜段的一页。

    走 ``idx_fact_dropped_segment (site, period_type, anchor_date, last_ranking)``——
    索引顺序即分页顺序，计划里不该出现排序节点。加 ``keyword_id`` 兜底保证分页稳定
    （同名次的行在页与页之间必须有确定顺序，否则翻页会重复或漏行）。

    掉榜行**只关联关键词维度**：它的当期字段全部为空，没有商品/品牌/类目可关联。
    """
    return (
        select(
            FactDropped.keyword_id.label("keyword_id"),
            DimKeyword.keyword.label("keyword"),
            FactDropped.last_seen_date.label("last_seen_date"),
            FactDropped.last_ranking.label("last_ranking"),
        )
        .select_from(FactDropped)
        .join(DimKeyword, DimKeyword.keyword_id == FactDropped.keyword_id)
        .where(
            FactDropped.site == site,
            FactDropped.period_type == view,
            FactDropped.anchor_date == on,
        )
        .order_by(FactDropped.last_ranking.asc(), FactDropped.keyword_id.asc())
        .offset(offset)
        .limit(limit)
    )


def shape_dropped_rows(rows) -> list[dict[str, Any]]:
    """掉榜段行 → 行字典。当期字段留空，由 schema 的默认值承担。"""
    return [dict(r._mapping) | {"is_dropped": True} for r in rows]


class FactRowDims(NamedTuple):
    """一行结果所需的维度别名。

    调用方各建一组（``new_fact_row_dims()``），再交给 ``fact_metric_columns`` 与
    ``join_fact_row_dims``。三者必须拿同一组，否则 SELECT 里引用的别名不在 FROM 里。
    """

    p2: Any
    p3: Any
    b1: Any
    b2: Any
    b3: Any
    g1: Any
    g2: Any
    g3: Any


def new_fact_row_dims() -> FactRowDims:
    return FactRowDims(
        p2=aliased(DimProduct),
        p3=aliased(DimProduct),
        b1=aliased(DimBrand),
        b2=aliased(DimBrand),
        b3=aliased(DimBrand),
        g1=aliased(DimCategory),
        g2=aliased(DimCategory),
        g3=aliased(DimCategory),
    )


def fact_metric_columns(fact, dims: FactRowDims) -> list:
    """一行结果的**指标列**（不含 keyword_id / keyword 这两个身份列）。

    **这份投影只有这一处定义**，分析查询屏与词库盯盘屏共用。两屏若各写一份，分叉不会
    报错——表现是同一个词在两屏显示不同的值，而没有任何测试会指向这里。身份列留给调用方
    自己给：分析屏从事实表取，词库盯盘从条目表取（那边事实行可能整行为空）。

    三个商品槽位的关联方式**刻意不对称**：top1 用未取别名的 ``DimProduct``，top2/top3
    各用一个 alias。因为 ``_apply_filters`` 里剩余的商品维度筛选（ASIN、标题、份额）都写在
    ``DimProduct`` 上，这样一来 alias 对筛选代码**不可见**——想让它们扩散到第二、第三名，
    必须显式改 ``_apply_filters`` 去引用 alias。口径由此是结构事实，而不是一条后来者会
    无意打破的约定。
    """
    return [
        fact.current_ranking.label("current_ranking"),
        fact.previous_ranking.label("previous_ranking"),
        fact.ranking_change.label("ranking_change"),
        fact.is_new.label("is_new"),
        DimProduct.asin.label("top1_asin"),
        DimProduct.title.label("top1_title"),
        fact.top1_click_share.label("top1_click_share"),
        fact.top1_conversion_share.label("top1_conversion_share"),
        dims.p2.asin.label("top2_asin"),
        dims.p2.title.label("top2_title"),
        fact.top2_click_share.label("top2_click_share"),
        fact.top2_conversion_share.label("top2_conversion_share"),
        dims.p3.asin.label("top3_asin"),
        dims.p3.title.label("top3_title"),
        fact.top3_click_share.label("top3_click_share"),
        fact.top3_conversion_share.label("top3_conversion_share"),
        # 关键词级的前三品牌与前三类目，名次由列位置承载
        dims.b1.name.label("top1_brand"),
        dims.b2.name.label("top2_brand"),
        dims.b3.name.label("top3_brand"),
        dims.g1.name.label("top1_category"),
        dims.g2.name.label("top2_category"),
        dims.g3.name.label("top3_category"),
    ]


def join_fact_row_dims(stmt, fact, dims: FactRowDims):
    """把 ``fact_metric_columns`` 引用到的维度表按主键 LEFT JOIN 进来。

    全部是按主键的外连接：每页几十行即几十次主键探查，相对事实表的分区扫描可忽略；
    且这些 alias 只出现在 SELECT 与 JOIN 条件里、不进 WHERE，故不改变既有执行计划。

    **品牌与类目不走商品槽位那套结构**：它们是关键词级的独立排行，直接挂在事实表的六个
    引用列上，筛选是"前三任一命中"（见 ``_apply_filters``），与商品槽位无关。
    """
    return (
        stmt.join(DimProduct, DimProduct.product_id == fact.top1_product_id, isouter=True)
        .join(dims.p2, dims.p2.product_id == fact.top2_product_id, isouter=True)
        .join(dims.p3, dims.p3.product_id == fact.top3_product_id, isouter=True)
        .join(dims.b1, dims.b1.brand_id == fact.top1_brand_id, isouter=True)
        .join(dims.b2, dims.b2.brand_id == fact.top2_brand_id, isouter=True)
        .join(dims.b3, dims.b3.brand_id == fact.top3_brand_id, isouter=True)
        .join(dims.g1, dims.g1.category_id == fact.top1_category_id, isouter=True)
        .join(dims.g2, dims.g2.category_id == fact.top2_category_id, isouter=True)
        .join(dims.g3, dims.g3.category_id == fact.top3_category_id, isouter=True)
    )


def _base_query(site: str, view: str, on: date):
    """作用域恒定的 base query：单站点单日期切片 JOIN 维度。

    列投影与维度关联由 ``fact_metric_columns`` / ``join_fact_row_dims`` 提供，与词库盯盘屏
    共用同一份——两屏各写一份的话，分叉的表现是同一个词在两屏显示不同的值，且不报错。
    """
    fact, date_col = fact_and_datecol(view)
    dims = new_fact_row_dims()
    stmt = (
        select(
            fact.keyword_id.label("keyword_id"),
            DimKeyword.keyword.label("keyword"),
            *fact_metric_columns(fact, dims),
        )
        .select_from(fact)
        .join(DimKeyword, DimKeyword.keyword_id == fact.keyword_id)
    )
    stmt = join_fact_row_dims(stmt, fact, dims).where(fact.site == site, date_col == on)
    return stmt, fact


def _count_base_query(site: str, view: str, on: date):
    """计数专用 base query：只关联筛选真正用到的维度（关键词 + top1 商品）。

    与 ``_base_query`` 分开，是因为计数要扫遍命中行（精确计数，不封顶），而 top2/top3
    的关联对"有多少行"毫无贡献（LEFT JOIN 主键不改变行数），带上就是纯粹的开销。
    筛选条件引用的维度在两条路径上完全一致，故 ``_apply_filters`` 两边通用。

    **这个 ``DimProduct`` LEFT JOIN 不能删**：ASIN 与「竞对新发现」筛选已经改成不相关子
    查询，不再依赖它，但 ``product_title`` 筛选仍然只作用于 top1、仍然借用这个别名
    （issue #56 P2 design.md 决策 2）。
    """
    fact, date_col = fact_and_datecol(view)
    stmt = (
        select(fact.keyword_id.label("keyword_id"))
        .select_from(fact)
        .join(DimKeyword, DimKeyword.keyword_id == fact.keyword_id)
        .join(DimProduct, DimProduct.product_id == fact.top1_product_id, isouter=True)
        .where(fact.site == site, date_col == on)
    )
    return stmt, fact


def _apply_filters(stmt, fact, req: AnalysisSearchRequest, *, site: str, user_id: int):
    """在 base query 上 AND 叠加筛选。不触碰站点/日期边界。

    **ASIN 与「竞对新发现」按前三商品槽位任一命中**（与品牌/类目同一形状：解析成
    ``product_id``/id 集合的子查询 → 三列 ``OR``），走独立的 ``idx_{prefix}_top{n}_product``
    三个索引（issue #56 P2）。``product_title`` 仍然只作用于点击量第一名——它借用的是
    base query 里已 JOIN 到 ``top1_product_id`` 的那个 ``DimProduct`` 别名，本次不扩展。
    要扩到三个槽位必须显式改写本函数，不会因为 base query 多返回了字段而悄悄扩散。

    ``user_id`` 只用于「竞对新发现」筛选的关注关系判定，从 service 层的当前用户令牌传入，
    MUST NOT 从请求体读——与本仓库既有鉴权模式一致（词库/竞对监控同一条纪律）。
    """
    if req.keyword:
        stmt = stmt.where(DimKeyword.keyword.ilike(f"%{req.keyword}%"))
    # 品牌与类目：**前三任一命中**。它们是关键词级排行，挂在事实表的六个引用列上，
    # 与商品槽位无关。语义是「这个词的前三品牌里有 X」——展示口径与筛选口径由此一致。
    #
    # 先在维度表上把名称解析成 id 集合（维度表小，走唯一索引），再在事实表上对三列做
    # IN——三列 OR 的索引方案待实测落定（见变更第 4 组），此处只保证语义正确。
    if req.brands:
        ids = select(DimBrand.brand_id).where(DimBrand.name.in_(req.brands)).scalar_subquery()
        stmt = stmt.where(
            or_(fact.top1_brand_id.in_(ids), fact.top2_brand_id.in_(ids),
                fact.top3_brand_id.in_(ids))
        )
    if req.categories:
        ids = select(DimCategory.category_id).where(
            DimCategory.name.in_(req.categories)
        ).scalar_subquery()
        stmt = stmt.where(
            or_(fact.top1_category_id.in_(ids), fact.top2_category_id.in_(ids),
                fact.top3_category_id.in_(ids))
        )
    # ASIN 是精确码，**等值匹配**：先解析出单个 product_id（走 `uq_dim_product_asin`
    # 唯一索引），再对三个商品槽位做 OR——之前只匹配 top1，把一个实际排第二、第三名次
    # 的 ASIN 粘进搜索框会查不到任何结果，这个限制对用户不可见（issue #56 P2）。
    # 大小写与空白已由请求模型规范化（见 AnalysisSearchRequest._normalise_asin），
    # 这里不再兜一遍——两处各规范一次，改动时漏掉一处就会静默分叉。
    if req.asin:
        pid = select(DimProduct.product_id).where(DimProduct.asin == req.asin).scalar_subquery()
        stmt = stmt.where(
            or_(fact.top1_product_id == pid, fact.top2_product_id == pid, fact.top3_product_id == pid)
        )
    if req.product_title:
        stmt = stmt.where(DimProduct.title.ilike(f"%{req.product_title}%"))
    # 竞对新发现：命中口径是该词前三商品槽位任一 ASIN，属于当前用户在当前站点关注的卖家
    # **近 NEW_DISCOVERY_WINDOW_DAYS 天内新发现**的 ASIN（不是"曾经上报过就算"——
    # `competitor_seller_asin` 不做下架删除，无时间限定的话这个开关会命中卖家有史以来
    # 上报过的全部商品，PR #66 复审 P1 指出）。判据与卖家清单的
    # 「近 7 日新发现」（`watch_crud._new_discoveries_subq`）同构并复用同一个窗口常量：
    # `first_seen_at > baseline_at`（基线批次不算新发现）且落在窗口内；`baseline_at`
    # 为 NULL（尚未建立基线）时比较结果是 SQL NULL，天然不命中，不需要额外的 CASE。
    if req.competitor_new:
        watched_product_ids = (
            select(DimProduct.product_id)
            .join(CompetitorSellerAsin, CompetitorSellerAsin.asin == DimProduct.asin)
            .join(
                CompetitorSeller,
                and_(
                    CompetitorSeller.seller_id == CompetitorSellerAsin.seller_id,
                    CompetitorSeller.site == CompetitorSellerAsin.site,
                ),
            )
            .join(
                UserSellerWatch,
                and_(
                    UserSellerWatch.seller_id == CompetitorSellerAsin.seller_id,
                    UserSellerWatch.site == CompetitorSellerAsin.site,
                ),
            )
            .where(
                UserSellerWatch.user_id == user_id,
                CompetitorSellerAsin.site == site,
                CompetitorSellerAsin.first_seen_at > CompetitorSeller.baseline_at,
                CompetitorSellerAsin.first_seen_at
                > func.now() - text(f"interval '{NEW_DISCOVERY_WINDOW_DAYS} days'"),
            )
            .scalar_subquery()
        )
        stmt = stmt.where(
            or_(
                fact.top1_product_id.in_(watched_product_ids),
                fact.top2_product_id.in_(watched_product_ids),
                fact.top3_product_id.in_(watched_product_ids),
            )
        )
    if req.ranking_min is not None:
        stmt = stmt.where(fact.current_ranking >= req.ranking_min)
    if req.ranking_max is not None:
        stmt = stmt.where(fact.current_ranking <= req.ranking_max)
    if req.change_min is not None:
        stmt = stmt.where(fact.ranking_change >= req.change_min)
    if req.change_max is not None:
        stmt = stmt.where(fact.ranking_change <= req.change_max)
    if req.click_share_min is not None:
        stmt = stmt.where(fact.top1_click_share >= req.click_share_min)
    if req.click_share_max is not None:
        stmt = stmt.where(fact.top1_click_share <= req.click_share_max)
    if req.conversion_share_min is not None:
        stmt = stmt.where(fact.top1_conversion_share >= req.conversion_share_min)
    if req.conversion_share_max is not None:
        stmt = stmt.where(fact.top1_conversion_share <= req.conversion_share_max)
    if req.is_new is not None:
        stmt = stmt.where(fact.is_new == req.is_new)
    return stmt


def _apply_order(stmt, fact, req: AnalysisSearchRequest):
    """排序白名单：非法字段回落默认（当期排名升序）。"""
    field = req.orderBy if req.orderBy in SORTABLE_FIELDS else _DEFAULT_ORDER
    col = getattr(fact, field)
    direction = req.orderDir or ("asc" if field == _DEFAULT_ORDER else "desc")
    col = col.asc() if direction == "asc" else col.desc()
    # 加 keyword_id 兜底，保证分页稳定
    return stmt.order_by(col, fact.keyword_id.asc())


def search_stmt(
    site: str, view: str, on: date, req: AnalysisSearchRequest,
    *, offset: int | None = None, limit: int | None = None, user_id: int,
):
    """在榜段的一页（含筛选/排序/分页）。

    ``offset``/``limit`` 显式给出时优先于 ``req`` 的分页字段——分段分页下这一段取的
    未必是整页（跨界时只取到在榜段末尾），偏移与条数由 ``row_segments.plan_segments``
    算出，不能再从页码反推。``user_id`` 只给「竞对新发现」筛选用，见 ``_apply_filters``。
    """
    stmt, fact = _base_query(site, view, on)
    stmt = _apply_filters(stmt, fact, req, site=site, user_id=user_id)
    stmt = _apply_order(stmt, fact, req)
    if offset is None:
        offset = (req.page - 1) * req.perPage
    if limit is None:
        limit = req.perPage
    return stmt.offset(offset).limit(limit)


def count_capped_stmt(
    site: str, view: str, on: date, req: AnalysisSearchRequest, cap: int, user_id: int,
):
    """作用域内筛选后的行数，最多数到 ``cap``（数满即返回 cap，不再往下扫）。

    **只给导出的上限检查用，不要拿它当列表总数。** 那里问的是"超没超过 EXPORT_ROW_CAP"
    ——一个是非题，数满即可停；答案也只用于决定放行还是 413，不会作为数字呈现给用户。
    列表总数是另一回事，必须精确，见 :func:`count_filtered_stmt`。
    """
    stmt, fact = _count_base_query(site, view, on)
    stmt = _apply_filters(stmt, fact, req, site=site, user_id=user_id)
    capped = stmt.limit(cap).subquery()
    return select(func.count()).select_from(capped)


def count_filtered_stmt(site: str, view: str, on: date, req: AnalysisSearchRequest, user_id: int):
    """有筛选时的列表总数：**精确计数，不封顶**（理由见文件头 FILTERED_COUNT_CAP 的墓志铭）。

    代价由切片大小决定、不随保留窗口增长；实测热缓存下日报 168ms、周报 242–375ms。
    换来的是 total 可以被当成真数使用——翻页器据此算页数，用户才够得着全部命中行。
    """
    stmt, fact = _count_base_query(site, view, on)
    stmt = _apply_filters(stmt, fact, req, site=site, user_id=user_id)
    return select(func.count()).select_from(stmt.subquery())


def export_stmt(site: str, view: str, on: date, req: AnalysisSearchRequest, limit: int, user_id: int):
    """导出用语句：按作用域与筛选取行，带服务端游标的执行选项。

    ``yield_per`` 触发 ``stream_results``，内存与块大小同阶、不随结果集增长——GB 级
    切片下整表 fetch 会撑爆 8 GiB 的应用服务器。调用方按 partition 分块消费，
    好让上层能按块批量补齐当前视图的搜索趋势。
    """
    stmt, fact = _base_query(site, view, on)
    stmt = _apply_filters(stmt, fact, req, site=site, user_id=user_id)
    stmt = _apply_order(stmt, fact, req)
    return stmt.limit(limit).execution_options(yield_per=EXPORT_CHUNK_SIZE)


# 搜索趋势的回溯规格：视图 → (事实表, 日期列, 趋势回溯天数)。
#
# 两个视图的差异恰好只有这三个值，其余 SQL 一字不差，故参数化而不是各写一条语句——
# 并列的两条语句会让"回溯多远"存两份，改一处忘一处不报错，只是两个视图悄悄不一致。
#
# 趋势渲染的点数：日 7 个（回溯 6 天），周 4 个（回溯 21 天 = 3 周）。
#
# **趋势窗口与行窗口一致**（日 7 天、周 4 周）：小图回答的是「这批行所在的这段时间里，
# 这个词怎么走的」，与行集合同一个区间才对得上。它与历史排名列的参照点是两回事——
# 参照点可以更远（周视图最远 12 周前），由 fetch_lookback_days 统一取大者，一次回溯
# 同时喂两处，不多发查询。
#
# 用日期范围而非"最近 N 期"，是因为范围条件能下推做分区裁剪，而按 keyword 分组取前 N
# 需要窗口函数且裁不了分区。
_TREND_SPEC: dict[str, tuple[Any, Any, int]] = {
    "DAY": (FactDaily, FactDaily.report_date, 6),
    "WEEK": (FactWeekly, FactWeekly.report_date_week, 21),
}

# 历史排名的三个参照点，以**距锚定期的天数**表示：
#   日视图 昨天 / 3 天前 / 7 天前
#   周视图 上周 / 4 周前 / 12 周前
#
# 参照点允许落在趋势渲染窗口之外（日视图的 7 天前就是），它们只对已选定的行做补齐，
# 不参与决定行集合。
HISTORY_OFFSETS: dict[str, tuple[int, int, int]] = {
    "DAY": (1, 3, 7),
    "WEEK": (7, 28, 84),
}


def fetch_lookback_days(view: str) -> int:
    """一次回溯查询实际要取多远：趋势窗口与最远参照点取大者。

    这是"历史排名不单独发一条查询"的关键——一次回溯同时喂趋势与历史排名。两个视图都
    要多取：日视图趋势要 6 天、参照点最远 7 天；周视图趋势要 21 天（4 点）、参照点最远
    84 天（12 周前）。多取的那部分只进历史排名列，不进趋势小图，也不影响行集合。
    """
    _, _, trend_lookback = _TREND_SPEC[view]
    return max(trend_lookback, max(HISTORY_OFFSETS[view]))


def trends_stmt(site: str, keyword_ids: list[int], on: date, view: str):
    """搜索趋势的语句：对本页 keyword_id 回溯查该视图的事实表。

    走 idx_fact_{daily,weekly}_keyword_history（site, keyword_id, 日期列）——两张事实表
    的索引由同一个 `_fact_indexes` 生成，故两个视图走的是同构的路径。日期范围条件保证
    分区裁剪。历史不足就少几个点，不补齐。

    未知视图码直接 KeyError：静默回落到日报会让周视图显示另一个周期的数据，
    而那种错误在界面上完全看不出来。
    """
    fact, date_col, _ = _TREND_SPEC[view]
    # 回溯范围盖住最远参照点，一次查询同时喂趋势与历史排名（见 fetch_lookback_days）。
    start = on - timedelta(days=fetch_lookback_days(view))
    return (
        select(fact.keyword_id, date_col, fact.current_ranking)
        .where(
            fact.site == site,
            fact.keyword_id.in_(keyword_ids),
            date_col >= start,
            date_col <= on,
        )
        .order_by(fact.keyword_id, date_col)
    )


def slice_has_brand_data_stmt(site: str, view: str, on: date):
    """该切片是否已有品牌数据（LIMIT 1 探一行，不做全切片聚合）。

    只探 top1：某个关键词可能确实只有一个品牌，但一整个切片不可能所有关键词的第一品牌
    都为空——除非它还没重导过。
    """
    fact, date_col = fact_and_datecol(view)
    return (
        select(fact.keyword_id)
        .where(fact.site == site, date_col == on, fact.top1_brand_id.isnot(None))
        .limit(1)
    )


def category_options_stmt(site: str, view: str, on: date):
    """类目下拉：读 category_stats，按作用域三元组返回选项与计数。"""
    return _stats_options_stmt("category_stats", "dim_category", "category_id", site, view, on)


def brand_options_stmt(site: str, view: str, on: date):
    """品牌下拉：读 brand_stats，与类目同构。

    品牌改为精确多选后，**计数不是装饰**：品牌名高度相近（Anker / Anker Direct /
    Ankermann），计数是用户判断该选哪个的唯一依据——子串搜索时代这个判断是由结果集
    替他做的。
    """
    return _stats_options_stmt("brand_stats", "dim_brand", "brand_id", site, view, on)


def _stats_options_stmt(
    table: str, dim_table: str, id_col: str, site: str, view: str, on: date
):
    """两个下拉共用：两张统计表结构同构，只差表名与维度。

    **必须带日期。** 统计按 (站点, 周期, 日期) 分片，与筛选的作用域三元组严格同粒度——
    少一个日期条件就会把整个保留窗口的分片全加起来，得到一个比实际筛选结果大得多的数，
    而那正是改造前的缺陷（实测两天数据：下拉 21944 / 实筛 17497）。

    统计表存维度 id 不存名称（按日期分片后行数与日期数相乘，存名称等于把同一字符串
    重复几千万次），故读时回 join 维度表——取的是一个切片，join 走主键，代价可忽略。
    """
    return text(
        f"SELECT d.name, s.cnt FROM {_SCHEMA}.{table} s "
        f"JOIN {_SCHEMA}.{dim_table} d ON d.{id_col} = s.{id_col} "
        f"WHERE s.site = :site AND s.period_type = :view AND s.report_date = :on "
        f"ORDER BY s.cnt DESC"
    ).bindparams(site=site, view=view, on=on)


# --------------------------------------------------------------------------
# 整形：把结果行转成上层要的形状。纯函数，与执行模型无关。
# --------------------------------------------------------------------------


def shape_rows(rows) -> list[dict[str, Any]]:
    """结果行 → dict 列表。"""
    return [dict(row._mapping) for row in rows]


def _trend_window_start(on: date | None, view: str | None) -> date | None:
    """趋势渲染窗口的起点。``on``/``view`` 缺省时不裁（旧调用方与直接测形状的用法）。"""
    if on is None or view is None:
        return None
    _, _, trend_lookback = _TREND_SPEC[view]
    return on - timedelta(days=trend_lookback)


def shape_trends(rows, on: date | None = None, view: str | None = None) -> dict[int, list[int]]:
    """趋势查询行 → {keyword_id: [排名…]}（按日期升序，依赖语句已排序）。

    回溯查询取的范围可能比趋势要画的**多一期**（日视图为了盖住 7 天前的参照点），
    多出来那期必须裁掉，否则日视图会画出 8 个点而规格说的是 7 个。
    """
    start = _trend_window_start(on, view)
    out: dict[int, list[int]] = {}
    for row in rows:
        kid, report_date, rank = row[0], row[1], row[-1]
        if start is not None and report_date < start:
            continue
        out.setdefault(kid, []).append(rank)
    return out


def shape_trend_dates(
    rows, on: date | None = None, view: str | None = None
) -> dict[int, list[date]]:
    """(keyword_id, report_date, ranking) 行 → {keyword_id: [日期…]}。

    与 :func:`shape_trends` 用同一个裁剪窗口——两者的点必须一一对应，各裁各的会让
    某个点的排名配上另一期的日期。
    """
    start = _trend_window_start(on, view)
    out: dict[int, list[date]] = {}
    for kid, report_date, _rank in rows:
        if start is not None and report_date < start:
            continue
        out.setdefault(kid, []).append(report_date)
    return out


def shape_history(rows, on: date, view: str) -> dict[int, list[int | None]]:
    """回溯行 → {keyword_id: [上期, 4 期前, 12 期前]}，取自同一次回溯查询。

    参照期缺失（未导入，或已被三个月保留窗口清理移出）时给 ``None`` 而非 0——0 在这份
    数据里已经是"无上期"的编码（见 ``previous_ranking`` 的默认值），两个含义挤在同一个
    值上会让界面无从区分"那天排第 0"和"那天没数据"。
    """
    by_keyword_date: dict[int, dict[date, int]] = {}
    for kid, report_date, rank in rows:
        by_keyword_date.setdefault(kid, {})[report_date] = rank

    offsets = HISTORY_OFFSETS[view]
    return {
        kid: [dates.get(on - timedelta(days=off)) for off in offsets]
        for kid, dates in by_keyword_date.items()
    }


def shape_category_options(rows) -> list[dict[str, Any]]:
    """(top_category, cnt) 行 → 下拉选项列表。"""
    return [{"category": r[0], "count": r[1]} for r in rows]


def shape_brand_options(rows) -> list[dict[str, Any]]:
    """(top_brand, cnt) 行 → 下拉选项列表。"""
    return [{"brand": r[0], "count": r[1]} for r in rows]


# --------------------------------------------------------------------------
# 同步执行器：导出路径与迁移/脚本用。
#
# 面向请求的查询路径走异步，直接用上面的构造函数 + ``await session.execute(...)``。
# 两条路径共用同一批构造函数——SQL 只有一处定义，作用域边界、筛选、排序白名单、
# 封顶计数不会漂移。差异被压缩到 ``await`` 与否这一行。
# --------------------------------------------------------------------------


def available_dates(db: Session, site: str, view: str) -> list[date]:
    return list(db.execute(available_dates_stmt(site, view)).scalars().all())


def scope_row_count(db: Session, site: str, view: str, on: date) -> int:
    return db.execute(scope_row_count_stmt(site, view, on)).scalar_one_or_none() or 0


def search_rows(
    db: Session, site: str, view: str, on: date, req: AnalysisSearchRequest, user_id: int
) -> list[dict[str, Any]]:
    return shape_rows(db.execute(search_stmt(site, view, on, req, user_id=user_id)).all())


def count_capped(
    db: Session, site: str, view: str, on: date, req: AnalysisSearchRequest, cap: int, user_id: int,
) -> int:
    return db.execute(count_capped_stmt(site, view, on, req, cap, user_id)).scalar_one()


def count_filtered(
    db: Session, site: str, view: str, on: date, req: AnalysisSearchRequest, user_id: int,
) -> int:
    return db.execute(count_filtered_stmt(site, view, on, req, user_id)).scalar_one()


def scope_dropped_count(db: Session, site: str, view: str, on: date) -> int:
    return db.execute(scope_dropped_count_stmt(site, view, on)).scalar_one_or_none() or 0


def iter_export_rows(
    db: Session, site: str, view: str, on: date, req: AnalysisSearchRequest, limit: int, user_id: int,
):
    """导出：分块产出（每块一个 dict 列表）。保持同步——服务端游标 + 自建会话。"""
    result = db.execute(export_stmt(site, view, on, req, limit, user_id))
    for partition in result.partitions():
        yield shape_rows(partition)


def iter_export_dropped_rows(db: Session, site: str, view: str, on: date, limit: int):
    """导出的掉榜段：与在榜段同样分块产出，顺序与列表一致（末尾、按最后在榜排名）。

    掉榜段没有筛选一说——筛选一律以锚定期那一行为准，而掉榜行在锚定期没有行可匹配，
    所以调用方在带筛选时根本不该走到这里。
    """
    result = db.execute(
        dropped_stmt(site, view, on, offset=0, limit=limit).execution_options(
            yield_per=EXPORT_CHUNK_SIZE
        )
    )
    for partition in result.partitions():
        yield shape_dropped_rows(partition)


def fetch_trends(
    db: Session, site: str, keyword_ids: list[int], on: date, view: str
) -> dict[int, list[int]]:
    """导出用的走势取值（同步）。

    ``on``/``view`` 必须一路传到 ``shape_trends``：回溯范围盖的是最远参照点，比趋势窗口
    宽（日 7 天 vs 6 天、周 84 天 vs 21 天），不裁就会把只该喂历史排名列的那几期画进走势。
    列表页走 ``_fetch_trends`` 已经裁过，这里漏裁的后果是导出比界面多点（日 8 vs 7、
    周 13 vs 4）——而导出的契约是"用户当下看到的那份数据"，两边点数不一致没有任何
    地方会报错。
    """
    if not keyword_ids:
        return {}
    rows = db.execute(trends_stmt(site, keyword_ids, on, view)).all()
    return shape_trends(rows, on=on, view=view)


def category_options(db: Session, site: str, view: str, on: date) -> list[dict[str, Any]]:
    return shape_category_options(db.execute(category_options_stmt(site, view, on)).all())
