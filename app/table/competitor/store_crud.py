"""独立站监控的数据访问层：只组装语句，**永不 commit**（事务边界归 service 层）。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.table.competitor.store_fetcher import ParsedProduct, ParsedStoreMeta
from app.table.competitor.store_model import (
    CompetitorStore,
    CompetitorStorePrice,
    CompetitorStoreProduct,
    CompetitorStoreRank,
    UserStoreWatch,
)
from app.table.competitor.watch_model import UserSellerGroup


class PriceSnapshot(NamedTuple):
    """一个变体最近一次观测到的取值，用于判断「这次要不要写」。"""

    price: Decimal | None
    compare_at_price: Decimal | None
    available: bool


def claim_due_store(session: Session, *, interval_hours: int, now: datetime) -> str | None:
    """认领一个到期站点，并**当场把它的拉取时刻推到现在**。

    ``FOR UPDATE SKIP LOCKED`` 是多 worker 安全的全部所需——不必像导入那样上全局咨询锁。
    导入串行化是因为两个 GB 级导入会争抢 WAL 与索引构建；这里一次写入几千行、瓶颈在
    网络，全局串行化只会让站点数增长时排队时间线性膨胀。

    **``last_fetch_at`` 记的是「上次尝试」不是「上次成功」，这是有意的。** 认领时就推进
    它，一个持续失败的站点才不会在每一轮轮询里被反复重抓——否则一个坏掉的站点会把整个
    轮询循环占满，其余站点永远轮不上。
    """
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
        .values(last_fetch_at=now)
    )
    return domain


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
    first_fetch_at: datetime | None = None,
) -> None:
    """回写拉取状态。``consecutive_failures`` 累加或清零，界面据此说「连续 N 次失败」。

    ``first_fetch_at`` 只在首刷时传，且用 ``COALESCE`` 保护——它一旦落定就不该被改动，
    改了会让首刷那批商品突然涌进上新流。
    """
    values: dict[str, object] = {
        "last_fetch_status": status,
        "catalog_complete": catalog_complete,
        "products_json_ok": products_json_ok,
        "rank_ok": rank_ok,
        "last_error": error,
        "consecutive_failures": (CompetitorStore.consecutive_failures + 1) if failed else 0,
    }
    if first_fetch_at is not None:
        values["first_fetch_at"] = func.coalesce(CompetitorStore.first_fetch_at, first_fetch_at)
    session.execute(
        update(CompetitorStore).where(CompetitorStore.domain == domain).values(**values)
    )


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


def mark_delisted(session: Session, domain: str, product_ids: set[int], *, at: datetime) -> None:
    """把本次完整拉取中未见到的商品判为下架。

    **只有在整次目录拉取完整成功时才可调用本函数**（判定在 service 层，见
    ``apply_fetch_result``）。带 ``delisted_at.is_(None)`` 是为了不覆盖首次判定的时刻。
    """
    if not product_ids:
        return
    session.execute(
        update(CompetitorStoreProduct)
        .where(
            CompetitorStoreProduct.domain == domain,
            CompetitorStoreProduct.product_id.in_(product_ids),
            CompetitorStoreProduct.delisted_at.is_(None),
        )
        .values(delisted_at=at)
    )


def clear_delisted(session: Session, domain: str, product_ids: set[int]) -> None:
    """商品重新上架：清掉下架标记。条件更新，只碰真正需要改的那几行。"""
    if not product_ids:
        return
    session.execute(
        update(CompetitorStoreProduct)
        .where(
            CompetitorStoreProduct.domain == domain,
            CompetitorStoreProduct.product_id.in_(product_ids),
            CompetitorStoreProduct.delisted_at.is_not(None),
        )
        .values(delisted_at=None)
    )


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
    """每个商品的当前价：取该商品所有变体里 ``observed_at`` 最大的那条。"""
    return (
        select(
            CompetitorStorePrice.product_id,
            CompetitorStorePrice.price,
            CompetitorStorePrice.compare_at_price,
            CompetitorStorePrice.available,
        )
        .where(CompetitorStorePrice.domain == domain)
        .distinct(CompetitorStorePrice.product_id)
        .order_by(CompetitorStorePrice.product_id, CompetitorStorePrice.observed_at.desc())
        .subquery()
    )


def products_stmt(domain: str, *, include: str, new_only: bool, days: int = 7):
    price = _latest_price_stmt(domain)
    rank = (
        select(CompetitorStoreRank.product_id, CompetitorStoreRank.rank)
        .where(CompetitorStoreRank.domain == domain)
        .distinct(CompetitorStoreRank.product_id)
        .order_by(CompetitorStoreRank.product_id, CompetitorStoreRank.captured_on.desc())
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


def new_arrivals_stmt(user_id: int, *, days: int):
    """上新流：**排除首刷那批**。

    判据是 ``first_seen_at > first_fetch_at``——首刷时两者相等，故首刷那批天然被排除。
    没有 ``first_fetch_at`` 这一列就无从区分「刚开始看」与「今天上新」，表现是新纳入
    一个站点当天涌出几百条假上新。
    """
    return (
        select(
            CompetitorStoreProduct,
            CompetitorStore.name.label("store_name"),
        )
        .join(CompetitorStore, CompetitorStore.domain == CompetitorStoreProduct.domain)
        .join(UserStoreWatch, UserStoreWatch.domain == CompetitorStoreProduct.domain)
        .where(
            UserStoreWatch.user_id == user_id,
            CompetitorStore.first_fetch_at.is_not(None),
            CompetitorStoreProduct.first_seen_at > CompetitorStore.first_fetch_at,
            CompetitorStoreProduct.first_seen_at > func.now() - text(f"interval '{days} days'"),
        )
        .order_by(CompetitorStoreProduct.first_seen_at.desc())
    )


def price_history_stmt(domain: str, product_id: int):
    return (
        select(CompetitorStorePrice)
        .where(
            CompetitorStorePrice.domain == domain,
            CompetitorStorePrice.product_id == product_id,
        )
        .order_by(CompetitorStorePrice.observed_at)
    )


def rank_history_stmt(domain: str, product_id: int):
    return (
        select(CompetitorStoreRank.captured_on, CompetitorStoreRank.rank)
        .where(
            CompetitorStoreRank.domain == domain,
            CompetitorStoreRank.product_id == product_id,
        )
        .order_by(CompetitorStoreRank.captured_on)
    )
