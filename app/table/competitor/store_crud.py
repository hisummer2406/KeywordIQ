"""独立站监控的数据访问层：只组装语句，**永不 commit**（事务边界归 service 层）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy import (
    Text,
    case,
    cast,
    delete,
    false,
    func,
    literal,
    null,
    select,
    text,
    union_all,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.table.competitor.asin_model import CompetitorSellerAsin
from app.table.competitor.seller_model import CompetitorSeller
from app.table.competitor.store_fetcher import ParsedProduct, ParsedStoreMeta
from app.table.competitor.store_model import (
    CompetitorStore,
    CompetitorStorePrice,
    CompetitorStoreProduct,
    CompetitorStoreProductEvent,
    CompetitorStoreRank,
    UserStoreWatch,
)
from app.table.competitor.watch_model import UserSellerGroup, UserSellerWatch

# 独立站认领专用的事务级咨询锁键（"KD"）。不与导入 0x4B49、运维动作 0x4B4F 共用。
FETCH_CLAIM_LOCK_KEY = 0x4B44
# 拉取租约时长。必须长于 service 层的单次拉取截止，否则租约过期时拉取可能还没结束，
# 第二个站点就会并发开拉。
FETCH_LEASE = timedelta(minutes=15)

EVENT_DELISTED = "DELISTED"
EVENT_RELISTED = "RELISTED"


class PriceSnapshot(NamedTuple):
    """一个变体最近一次观测到的取值，用于判断「这次要不要写」。"""

    price: Decimal | None
    compare_at_price: Decimal | None
    available: bool


def claim_due_store(session: Session, *, interval_hours: int, now: datetime) -> str | None:
    """认领一个到期站点，并**当场把它的拉取时刻推到现在**。

    **全局同一时刻只认领一个站点**：存在未过期租约时返回 None。原设计只用
    ``FOR UPDATE SKIP LOCKED``、刻意不全局串行（瓶颈在网络）；但目标站点按出口 IP 限流，
    4 个 worker 并发只把请求速率放大 4 倍、一起被限流（2026-11-04 生产实测）。串行用租约 +
    认领时的事务级咨询锁实现，**不**像导入那样用会话级锁包住整次工作——拉取是数分钟的
    网络等待，不该为它独占一条数据库连接。

    **``last_fetch_at`` 记的是「上次尝试」不是「上次成功」，这是有意的。** 认领时就推进
    它，一个持续失败的站点才不会在每一轮轮询里被反复重抓——否则一个坏掉的站点会把整个
    轮询循环占满，其余站点永远轮不上。
    """
    # **全局同一时刻只拉一个站点**（change serialize-dtc-fetch）。目标站点按出口 IP 限流，
    # 4 个 worker 并发只会把请求速率放大 4 倍。「有无未过期租约」的判定与写入用事务级咨询锁
    # 串行化——只靠 SKIP LOCKED，两个 worker 会同时看到「没有租约」并各认领一个不同站点。
    # 锁随本事务提交释放，持有时长只是这一次认领查询；拉取全程不持有任何连接或锁。
    if not session.execute(
        text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": FETCH_CLAIM_LOCK_KEY}
    ).scalar():
        return None
    leased = session.execute(
        select(CompetitorStore.domain)
        .where(CompetitorStore.fetch_lease_until > func.now())
        .limit(1)
    ).scalar_one_or_none()
    if leased is not None:
        return None

    stmt = (
        select(CompetitorStore.domain)
        .where(
            (CompetitorStore.last_fetch_at.is_(None))
            | (CompetitorStore.last_fetch_at < text(f"now() - interval '{interval_hours} hours'"))
        )
        .order_by(CompetitorStore.last_fetch_at.nulls_first())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    domain = session.execute(stmt).scalar_one_or_none()
    if domain is None:
        return None

    session.execute(
        update(CompetitorStore)
        .where(CompetitorStore.domain == domain)
        .values(last_fetch_at=now, fetch_lease_until=func.now() + FETCH_LEASE)
    )
    return domain


def release_fetch_lease(session: Session, domain: str) -> None:
    """清除租约。落库路径由 ``update_store_status`` 顺带清，这里供异常路径单独调用。"""
    session.execute(
        update(CompetitorStore)
        .where(CompetitorStore.domain == domain)
        .values(fetch_lease_until=None)
    )


def update_store_meta(session: Session, domain: str, meta: ParsedStoreMeta) -> None:
    """回写站点身份与币种。meta 拉取失败时调用方不调本函数，库里既有值原样保留。"""
    session.execute(
        update(CompetitorStore)
        .where(CompetitorStore.domain == domain)
        .values(
            name=meta.name,
            currency=meta.currency,
            country=meta.country,
            shop_id=meta.shop_id,
            myshopify_domain=meta.myshopify_domain,
            ships_to_countries=list(meta.ships_to_countries) or None,
        )
    )


def update_store_status(
    session: Session,
    domain: str,
    *,
    status: str,
    catalog_complete: bool,
    products_json_ok: bool,
    rank_ok: bool,
    error: str | None,
    failed: bool,
    baseline_at: datetime | None = None,
) -> None:
    """回写拉取状态。``consecutive_failures`` 累加或清零，界面据此说「连续 N 次失败」。

    ``baseline_at`` 只在**目录完整**时传（判定在 service 层），且用 ``COALESCE`` 保护——
    它一旦落定就不该被改动，改了会让基线前那批商品突然涌进上新流。
    """
    values: dict[str, object] = {
        "last_fetch_status": status,
        "catalog_complete": catalog_complete,
        "products_json_ok": products_json_ok,
        "rank_ok": rank_ok,
        "last_error": error,
        "consecutive_failures": (CompetitorStore.consecutive_failures + 1) if failed else 0,
        # 本次拉取到此结束，放下一个站点开拉。
        "fetch_lease_until": None,
    }
    if baseline_at is not None:
        values["baseline_at"] = func.coalesce(CompetitorStore.baseline_at, baseline_at)
    session.execute(
        update(CompetitorStore).where(CompetitorStore.domain == domain).values(**values)
    )


def store_baseline_at(session: Session, domain: str) -> datetime | None:
    """站点的上新基线（首次完整目录时刻）；NULL 表示还没完整拉取过。"""
    return session.execute(
        select(CompetitorStore.baseline_at).where(CompetitorStore.domain == domain)
    ).scalar_one_or_none()


def existing_product_ids(session: Session, domain: str) -> set[int]:
    """库里已有的商品 id。**新品判定的唯一依据**——不看 ``published_at``。"""
    rows = session.execute(
        select(CompetitorStoreProduct.product_id).where(CompetitorStoreProduct.domain == domain)
    ).scalars()
    return set(rows)


def insert_products(
    session: Session, domain: str, products: tuple[ParsedProduct, ...], *, seen_at: datetime
) -> None:
    """写入商品，已存在的**跳过不改**（本表只增不改）。"""
    if not products:
        return
    session.execute(
        pg_insert(CompetitorStoreProduct)
        .values(
            [
                {
                    "domain": domain,
                    "product_id": p.product_id,
                    "handle": p.handle,
                    "title": p.title,
                    "vendor": p.vendor,
                    "product_type": p.product_type,
                    "tags": list(p.tags) or None,
                    "image_url": p.image_url,
                    "published_at": p.published_at,
                    "source_created_at": p.created_at,
                    "source_updated_at": p.updated_at,
                    "first_seen_at": seen_at,
                }
                for p in products
            ]
        )
        .on_conflict_do_nothing(index_elements=["domain", "product_id"])
    )


def latest_prices(session: Session, domain: str) -> dict[int, PriceSnapshot]:
    """每个变体最近一次观测。走主键 ``(domain, variant_id, observed_at)`` 的 DISTINCT ON。"""
    stmt = (
        select(
            CompetitorStorePrice.variant_id,
            CompetitorStorePrice.price,
            CompetitorStorePrice.compare_at_price,
            CompetitorStorePrice.available,
        )
        .where(CompetitorStorePrice.domain == domain)
        .distinct(CompetitorStorePrice.variant_id)
        .order_by(CompetitorStorePrice.variant_id, CompetitorStorePrice.observed_at.desc())
    )
    return {
        row.variant_id: PriceSnapshot(row.price, row.compare_at_price, row.available)
        for row in session.execute(stmt)
    }


def insert_price_observations(session: Session, rows: list[dict]) -> None:
    """追加价格观测。调用方只传**发生了变化**的变体，本函数不做比对。"""
    if not rows:
        return
    session.execute(
        pg_insert(CompetitorStorePrice)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["domain", "variant_id", "observed_at"])
    )


def replace_ranks(
    session: Session, domain: str, *, captured_on: date, pairs: list[tuple[int, int]]
) -> None:
    """写当日名次快照。先删当日再写，同一天重跑即覆盖（``captured_on`` 是日期不是时间戳）。"""
    session.execute(
        delete(CompetitorStoreRank).where(
            CompetitorStoreRank.domain == domain,
            CompetitorStoreRank.captured_on == captured_on,
        )
    )
    if not pairs:
        return
    session.execute(
        pg_insert(CompetitorStoreRank).values(
            [
                {"domain": domain, "captured_on": captured_on, "rank": rank, "product_id": pid}
                for rank, pid in pairs
            ]
        )
    )


def _insert_events(
    session: Session, domain: str, product_ids: list[int], *, kind: str, at: datetime
) -> None:
    if not product_ids:
        return
    session.execute(
        pg_insert(CompetitorStoreProductEvent)
        .values(
            [
                {"domain": domain, "product_id": pid, "occurred_at": at, "kind": kind}
                for pid in product_ids
            ]
        )
        .on_conflict_do_nothing()
    )


def mark_delisted(session: Session, domain: str, product_ids: set[int], *, at: datetime) -> int:
    """把本次完整拉取中未见到的商品判为下架，并为**实际被改的行**各写一条下架事件。

    **只有在整次目录拉取完整成功时才可调用本函数**（判定在 service 层，见
    ``apply_fetch_result``）。带 ``delisted_at.is_(None)`` 是为了不覆盖首次判定的时刻；
    事件按 ``RETURNING`` 取，已下架的商品不会再写一条。返回实际判下架的数量。
    """
    if not product_ids:
        return 0
    changed = (
        session.execute(
            update(CompetitorStoreProduct)
            .where(
                CompetitorStoreProduct.domain == domain,
                CompetitorStoreProduct.product_id.in_(product_ids),
                CompetitorStoreProduct.delisted_at.is_(None),
            )
            .values(delisted_at=at)
            .returning(CompetitorStoreProduct.product_id)
        )
        .scalars()
        .all()
    )
    _insert_events(session, domain, list(changed), kind=EVENT_DELISTED, at=at)
    return len(changed)


def clear_delisted(
    session: Session, domain: str, product_ids: set[int], *, at: datetime
) -> int:
    """商品重新出现：清掉下架标记，并为实际被改的行各写一条重新出现事件。

    清掉 ``delisted_at`` 会丢掉「它曾下架」这件事——历史靠事件表保住，见
    ``CompetitorStoreProductEvent``。返回实际重新出现的数量。
    """
    if not product_ids:
        return 0
    changed = (
        session.execute(
            update(CompetitorStoreProduct)
            .where(
                CompetitorStoreProduct.domain == domain,
                CompetitorStoreProduct.product_id.in_(product_ids),
                CompetitorStoreProduct.delisted_at.is_not(None),
            )
            .values(delisted_at=None)
            .returning(CompetitorStoreProduct.product_id)
        )
        .scalars()
        .all()
    )
    _insert_events(session, domain, list(changed), kind=EVENT_RELISTED, at=at)
    return len(changed)


def listed_product_ids(session: Session, domain: str) -> set[int]:
    """库里当前未标记下架的商品 id，用于算本次的下架差集。"""
    rows = session.execute(
        select(CompetitorStoreProduct.product_id).where(
            CompetitorStoreProduct.domain == domain,
            CompetitorStoreProduct.delisted_at.is_(None),
        )
    ).scalars()
    return set(rows)


# ---------------------------------------------------------------------------
# 读取路径（异步）
#
# 与上面的写入路径分开：写入在后台轮询任务里、是批量的，用同步会话（与导入路径一致）；
# 读取在请求里、是并发的，用异步会话（与本仓库其余查询层一致）。两者共用同一批模型，
# 但不共用会话类型——混用会让后台任务占住异步池，而那个池是给查询与鉴权的。
# ---------------------------------------------------------------------------


def _new_in_7d_expr(days: int = 7):
    """近 N 日上架数。

    **按 ``published_at`` 而不是 ``first_seen_at``**——用户问的是「这家店最近一周上了
    多少新品」，那是数据源给的事实。按 ``first_seen_at`` 算的话，一个刚纳入监控的站点
    会把整个存量目录报成「近 7 日上新」。
    """
    return (
        select(func.count())
        .select_from(CompetitorStoreProduct)
        .where(
            CompetitorStoreProduct.domain == CompetitorStore.domain,
            CompetitorStoreProduct.delisted_at.is_(None),
            CompetitorStoreProduct.published_at
            > func.now() - text(f"interval '{days} days'"),
        )
        .correlate(CompetitorStore)
        .scalar_subquery()
    )


def _listed_count_expr():
    return (
        select(func.count())
        .select_from(CompetitorStoreProduct)
        .where(
            CompetitorStoreProduct.domain == CompetitorStore.domain,
            CompetitorStoreProduct.delisted_at.is_(None),
        )
        .correlate(CompetitorStore)
        .scalar_subquery()
    )


def watched_stores_stmt(user_id: int, *, group_id: int | None, ungrouped_only: bool):
    """当前用户关注的独立站。``user_id`` 是硬条件——私有性不靠界面不展示。"""
    stmt = (
        select(
            CompetitorStore,
            UserStoreWatch.group_id,
            UserStoreWatch.note,
            UserStoreWatch.created_at.label("watched_at"),
            UserSellerGroup.name.label("group_name"),
            _listed_count_expr().label("product_count"),
            _new_in_7d_expr().label("new_in_7d"),
        )
        .join(UserStoreWatch, UserStoreWatch.domain == CompetitorStore.domain)
        .outerjoin(UserSellerGroup, UserSellerGroup.group_id == UserStoreWatch.group_id)
        .where(UserStoreWatch.user_id == user_id)
    )
    if ungrouped_only:
        stmt = stmt.where(UserStoreWatch.group_id.is_(None))
    elif group_id is not None:
        stmt = stmt.where(UserStoreWatch.group_id == group_id)
    return stmt


def watched_domains_stmt(user_id: int):
    return select(UserStoreWatch.domain).where(UserStoreWatch.user_id == user_id)


def _latest_price_stmt(domain: str):
    """每个商品的当前价与当前可购买。

    - **价格**：该商品所有变体里 ``observed_at`` 最大的那条（口径未变；最低价/区间的
      展示留给商品工作台那一步定）。
    - **可购买**：先取每个变体的最新观测，再按商品 ``bool_or``——任一变体可买即可买。
      原先直接取「最近变动的那一个变体」的 ``available``，一个变体售罄就把整个商品显示成
      不可买（change fix-dtc-catalog-collection D5）。
    """
    per_variant = (
        select(
            CompetitorStorePrice.product_id,
            CompetitorStorePrice.observed_at,
            CompetitorStorePrice.price,
            CompetitorStorePrice.compare_at_price,
            CompetitorStorePrice.available,
        )
        .where(CompetitorStorePrice.domain == domain)
        .distinct(CompetitorStorePrice.variant_id)
        .order_by(CompetitorStorePrice.variant_id, CompetitorStorePrice.observed_at.desc())
        .subquery()
    )
    latest = (
        select(
            per_variant.c.product_id,
            per_variant.c.price,
            per_variant.c.compare_at_price,
        )
        .distinct(per_variant.c.product_id)
        .order_by(per_variant.c.product_id, per_variant.c.observed_at.desc())
        .subquery()
    )
    available = (
        select(
            per_variant.c.product_id,
            func.bool_or(per_variant.c.available).label("available"),
        )
        .group_by(per_variant.c.product_id)
        .subquery()
    )
    return (
        select(
            latest.c.product_id,
            latest.c.price,
            latest.c.compare_at_price,
            available.c.available,
        )
        .join(available, available.c.product_id == latest.c.product_id)
        .subquery()
    )


def products_stmt(domain: str, *, include: str, new_only: bool, days: int = 7):
    price = _latest_price_stmt(domain)
    # 当前名次**只取该站最新一次快照**。原先取「该商品最后一次上榜」的名次，两周前第 12、
    # 此后天天不在榜的商品会一直显示第 12（change fix-dtc-catalog-collection D5）。
    latest_day = (
        select(func.max(CompetitorStoreRank.captured_on))
        .where(CompetitorStoreRank.domain == domain)
        .scalar_subquery()
    )
    rank = (
        select(CompetitorStoreRank.product_id, CompetitorStoreRank.rank)
        .where(
            CompetitorStoreRank.domain == domain,
            CompetitorStoreRank.captured_on == latest_day,
        )
        .subquery()
    )
    stmt = (
        select(
            CompetitorStoreProduct,
            price.c.price,
            price.c.compare_at_price,
            price.c.available,
            rank.c.rank.label("latest_rank"),
        )
        .outerjoin(price, price.c.product_id == CompetitorStoreProduct.product_id)
        .outerjoin(rank, rank.c.product_id == CompetitorStoreProduct.product_id)
        .where(CompetitorStoreProduct.domain == domain)
    )
    if include == "LISTED":
        stmt = stmt.where(CompetitorStoreProduct.delisted_at.is_(None))
    elif include == "DELISTED":
        stmt = stmt.where(CompetitorStoreProduct.delisted_at.is_not(None))
    if new_only:
        stmt = stmt.where(
            CompetitorStoreProduct.published_at > func.now() - text(f"interval '{days} days'")
        )
    return stmt


def _window(days: int):
    return func.now() - text(f"interval '{days} days'")


@dataclass(frozen=True)
class ArrivalFilters:
    """商品发现的筛选（#166 D6）。全部可选；每一项都在 UNION **之前**施加到两侧。

    - ``source``：只看一侧。
    - ``site``：只作用于 Amazon 侧；给了站点时 DTC 侧整体不参与（Shopify 没有站点概念）。
    - ``group_id``：经各自的 watch 表过滤，天然限定在当前用户名下（调用方另核分组归属）。
    - ``seller_id`` / ``domain``：单个店铺。给了 ``seller_id`` 只剩 Amazon 侧，给了 ``domain``
      只剩 DTC 侧。
    """

    source: str | None = None
    site: str | None = None
    group_id: int | None = None
    seller_id: str | None = None
    domain: str | None = None

    @property
    def wants_dtc(self) -> bool:
        return self.source in (None, "DTC") and self.site is None and self.seller_id is None

    @property
    def wants_amazon(self) -> bool:
        return self.source in (None, "AMAZON") and self.domain is None


def _dtc_arrivals_select(user_id: int, days: int, filters: ArrivalFilters = ArrivalFilters()):
    """独立站那一侧的统一投影。

    **排除基线前那批**：判据是 ``first_seen_at > baseline_at``，基线是首次**完整**拿到
    目录的时刻——基线前（含落定那次）见到的商品都不算上新，哪怕它们分散在多次不完整
    拉取里陆续出现。没有基线就无从区分「刚开始看」与「今天上新」，
    表现是新纳入一个站点当天涌出几百条假上新。

    时间取 ``published_at``（数据源给的真实上架时间），缺失才退回 ``first_seen_at``，
    ``time_kind`` 随之标 ``PUBLISHED`` / ``DISCOVERED``。

    ``site`` 恒为 NULL：独立站商品的粒度是 ``(domain, product_id)``，没有站点这回事。
    这一列只在 Amazon 那侧非空——两侧投影列数与列名必须一致（UNION 的硬要求），故这里
    仍要占一列。
    """
    at = func.coalesce(CompetitorStoreProduct.published_at, CompetitorStoreProduct.first_seen_at)
    return (
        select(
            cast(literal("DTC"), Text).label("source"),
            cast(CompetitorStoreProduct.domain, Text).label("source_key"),
            cast(CompetitorStore.name, Text).label("source_name"),
            cast(null(), Text).label("site"),
            cast(CompetitorStoreProduct.product_id, Text).label("product_key"),
            cast(CompetitorStoreProduct.title, Text).label("title"),
            cast(CompetitorStoreProduct.image_url, Text).label("image_url"),
            cast(CompetitorStoreProduct.handle, Text).label("product_handle"),
            CompetitorStoreProduct.first_seen_at.label("discovered_at"),
            CompetitorStoreProduct.published_at.label("published_at"),
            at.label("at"),
            case(
                (CompetitorStoreProduct.published_at.is_not(None), literal("PUBLISHED")),
                else_=literal("DISCOVERED"),
            )
            .cast(Text)
            .label("time_kind"),
        )
        .join(CompetitorStore, CompetitorStore.domain == CompetitorStoreProduct.domain)
        .join(UserStoreWatch, UserStoreWatch.domain == CompetitorStoreProduct.domain)
        .where(
            UserStoreWatch.user_id == user_id,
            CompetitorStore.baseline_at.is_not(None),
            CompetitorStoreProduct.first_seen_at > CompetitorStore.baseline_at,
            CompetitorStoreProduct.first_seen_at > _window(days),
            *([UserStoreWatch.group_id == filters.group_id] if filters.group_id is not None else []),
            *([CompetitorStoreProduct.domain == filters.domain] if filters.domain is not None else []),
        )
    )


def _amazon_arrivals_select(user_id: int, days: int, filters: ArrivalFilters = ArrivalFilters()):
    """Amazon 卖家那一侧的统一投影。

    判据与独立站同构，只是基线列不同：``first_seen_at > baseline_at``（首次上报落定基线，
    该批与基线时刻严格相等，故恒不进流）。**时间语义永远是 ``DISCOVERED``**——被动采集
    证明不了商品何时上架，标成「上架于」等于拿「我们发现得晚」冒充「他上得晚」。

    两处退让写在这里，都是"目前只能这样"而不是设计选择：

    - ``source_name`` 恒为 NULL。``competitor_seller`` 里**没有卖家名这回事**——我们从未
      取过它。不拿 ``user_seller_watch.note`` 顶替：那是用户私有的备注，塞进一个在独立站
      那侧表示"店铺自己的名字"的列，会让同一个字段按行表示两种东西。界面照旧回落显示
      ``source_key``（seller_id）。将来采集端顺手报回卖家名时，它直接落进这一列。
    - ``title`` 用 ``COALESCE(title, asin)``：上报可以不带标题（``title`` 可空），而响应
      契约里 ``title`` 是必填。用 ASIN 兜底，不编造空串——空标题在界面上是一行看不出是
      什么的条目。

    ``site`` **必须选进来**：共享层的真实粒度是 ``(seller_id, site, asin)``，不是
    ``(seller_id, asin)``。同一用户同时关注同一个 seller_id 的 US、GB 两个站点、且两个
    站点都出现同一个 ASIN 时（不同市场跟卖同一个商品是常态），若响应身份只有
    ``source_key``（seller_id）+ ``product_key``（asin），两行在 API 与前端 rowKey 上
    完全无法区分；`new_arrivals_stmt` 的次级排序键同样要靠它才能恢复全序——否则这两行
    会被判成"同一行"，翻页时互相顶替。
    """
    return (
        select(
            cast(literal("AMAZON"), Text).label("source"),
            cast(CompetitorSellerAsin.seller_id, Text).label("source_key"),
            cast(CompetitorSeller.seller_name, Text).label("source_name"),
            cast(CompetitorSellerAsin.site, Text).label("site"),
            cast(CompetitorSellerAsin.asin, Text).label("product_key"),
            cast(func.coalesce(CompetitorSellerAsin.title, CompetitorSellerAsin.asin), Text).label(
                "title"
            ),
            cast(CompetitorSellerAsin.image_url, Text).label("image_url"),
            cast(null(), Text).label("product_handle"),
            CompetitorSellerAsin.first_seen_at.label("discovered_at"),
            cast(null(), CompetitorStoreProduct.published_at.type).label("published_at"),
            CompetitorSellerAsin.first_seen_at.label("at"),
            cast(literal("DISCOVERED"), Text).label("time_kind"),
        )
        .join(
            CompetitorSeller,
            (CompetitorSeller.seller_id == CompetitorSellerAsin.seller_id)
            & (CompetitorSeller.site == CompetitorSellerAsin.site),
        )
        .join(
            UserSellerWatch,
            (UserSellerWatch.seller_id == CompetitorSellerAsin.seller_id)
            & (UserSellerWatch.site == CompetitorSellerAsin.site),
        )
        .where(
            UserSellerWatch.user_id == user_id,
            CompetitorSeller.baseline_at.is_not(None),
            CompetitorSellerAsin.first_seen_at > CompetitorSeller.baseline_at,
            CompetitorSellerAsin.first_seen_at > _window(days),
            *([CompetitorSellerAsin.site == filters.site] if filters.site is not None else []),
            *([UserSellerWatch.group_id == filters.group_id] if filters.group_id is not None else []),
            *([CompetitorSellerAsin.seller_id == filters.seller_id] if filters.seller_id is not None else []),
        )
    )


def new_arrivals_stmt(user_id: int, *, days: int, filters: ArrivalFilters = ArrivalFilters()):
    """上新流：两类来源**在同一次查询内**合并后统一排序分页。

    **MUST NOT 各查一页再在应用层归并。** 各来源自己的偏移量与合并后的全局序号对不上，
    翻页时会重复或漏掉条目，而这种错误在界面上表现为「某条上新出现两次」，极难归因。

    **排序键必须与界面显示的那个时间是同一个。** 这里按 ``at`` 排（独立站是上架时间、
    Amazon 是发现时间），而不是两侧各按自己的 ``first_seen_at`` ——列表若不按自己显示的
    时间列排序，读起来就是坏的。

    > 这一条改动了独立站原先的行为：它此前按 ``first_seen_at`` 排却显示 ``published_at``，
    > 单来源时两者通常接近所以看不出来。**成员判定仍按 ``first_seen_at``**（"纳入监控后
    > 新出现了什么"），只有排序改了——两个口径不同不是疏漏，见 CLAUDE.md 独立站一节。

    **平局必须有确定的次序。** ``published_at`` 只精确到分钟，同一分钟内的多条商品极常见；
    没有次级排序键时，Postgres 对相同 ``at`` 的行不保证稳定顺序，同一行可能在第 1 页和
    第 2 页各出现一次——正是上面那条要防的失败形态，且它只在翻页时才现形。

    **次级键必须含 ``site``，否则对 Amazon 侧不是全序。** 共享层的粒度是
    ``(seller_id, site, asin)``；同一 seller_id 跨站点出现同一 ASIN 时，
    ``(source, source_key, product_key)`` 三者都相同，仍会撞出并列——补 ``site`` 才能
    恢复确定顺序。``site`` 对 DTC 侧恒为 NULL，Postgres 排序里 NULL 参与比较不影响
    Amazon 侧内部的确定性，两侧本就先按 ``source`` 分开。
    """
    # 被筛掉的一侧**不参与 UNION**（而不是两侧都查、外层再滤）：外层滤会让排序/分页在更大
    # 的集合上做，且两侧的过滤口径会分散到两处。两侧都被排除时（如来源=Shopify 又选了站点）
    # 保留一侧加恒假条件，让结果形状与计数路径不变、只是为空。
    sides = []
    if filters.wants_dtc:
        sides.append(_dtc_arrivals_select(user_id, days, filters))
    if filters.wants_amazon:
        sides.append(_amazon_arrivals_select(user_id, days, filters))
    if not sides:
        sides.append(_amazon_arrivals_select(user_id, days, filters).where(false()))
    unified = (union_all(*sides) if len(sides) > 1 else sides[0]).subquery()
    return select(unified).order_by(
        unified.c.at.desc(),
        unified.c.source,
        unified.c.source_key,
        unified.c.site,
        unified.c.product_key,
    )


def price_history_stmt(domain: str, product_id: int):
    """一个商品的调价序列，**倒序**——详情页要看的是"最近发生了什么"，不是从头翻起。

    唯一调用方是 `product_history`（PR #74 新增的详情抽屉），改这个排序不影响其他路径。
    """
    return (
        select(CompetitorStorePrice)
        .where(
            CompetitorStorePrice.domain == domain,
            CompetitorStorePrice.product_id == product_id,
        )
        .order_by(CompetitorStorePrice.observed_at.desc())
    )


def rank_history_stmt(domain: str, product_id: int):
    """一个商品的店内名次走势，**倒序**——理由同 `price_history_stmt`。"""
    return (
        select(CompetitorStoreRank.captured_on, CompetitorStoreRank.rank)
        .where(
            CompetitorStoreRank.domain == domain,
            CompetitorStoreRank.product_id == product_id,
        )
        .order_by(CompetitorStoreRank.captured_on.desc())
    )
