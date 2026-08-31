"""独立站监控的业务层：差分、完整性判定、状态回写、拉取编排。

**事务边界在这一层**，crud 层永不 commit。

拉取的执行形态见 ``refresh_due_store``：网络那段 ``await`` 在事件循环上，只有数据库
写入走 ``run_in_threadpool``。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.table.competitor import store_crud
from app.table.competitor.store_fetcher import (
    FETCH_FAILED,
    FETCH_NO_CATALOG_API,
    FETCH_OK,
    ParsedProduct,
    StoreFetchOutcome,
    fetch_store,
)
from config import settings
from database import SessionFactory

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class RefreshSummary:
    """一次拉取写入了什么，供日志与测试断言。"""

    domain: str
    status: str
    new_products: int
    price_changes: int
    ranks_written: int
    delisted: int
    relisted: int
    catalog_complete: bool
    rank_ok: bool


def _price_changed(before: store_crud.PriceSnapshot | None, variant) -> bool:
    """只有取值真的变了才写。

    这与 ``dim_product`` 的 UPSERT 上那句 ``WHERE title IS DISTINCT FROM EXCLUDED.title``
    是同一条纪律：PostgreSQL 的 UPDATE/INSERT 无论新旧值是否相同都要写元组并维护索引，
    无条件写入在这里是约 30 倍的写放大（17 家店 3360 万行/年 vs 约 100 万行/年），
    而其中约 99% 的行与前一日完全相同。
    """
    if before is None:
        return True
    return (
        before.price != variant.price
        or before.compare_at_price != variant.compare_at_price
        or before.available != variant.available
    )


def apply_fetch_result(
    session: Session,
    domain: str,
    outcome: StoreFetchOutcome,
    *,
    now: datetime,
    is_first_fetch: bool,
) -> RefreshSummary:
    """把一次拉取的产出落库。调用方负责 commit。

    四条约束，每条都对应一个「错了不会报错」的失败形态：

    1. **下架判定只在 ``catalog_complete`` 为真时做。** 不完整时照常写入见到的商品
       （只增，永远安全），但绝不判下架——一次网络抖动把整店判成清仓，是这条路唯一能
       造成大面积假数据的方式。
    2. **新品看 ``product_id`` 在不在库里，不看 ``published_at``。** 商户批量重新发布会
       让后者整体跳到当日，据此判定会伪造出一批假上新。
    3. **首刷不产生上新事件。** 新纳入站点上个月发的商品不是「今天上新」，只是我们刚
       开始看。但它们照常入库且 ``published_at`` 原样保留，所以「近 7 天上了什么」当场
       就能查——这正是与 Amazon 侧最大的差别，那边要等一个周期建基线。
    4. **价格只在变化时追加。** 见 ``_price_changed``。
    """
    known_ids = store_crud.existing_product_ids(session, domain)
    seen_ids = {p.product_id for p in outcome.products}
    new_ids = seen_ids - known_ids

    store_crud.insert_products(session, domain, outcome.products, seen_at=now)

    # 重新上架：本次见到、但库里标着下架的，清掉标记。条件更新，不碰其余行。
    listed_ids = store_crud.listed_product_ids(session, domain)
    relisted = (seen_ids & known_ids) - listed_ids
    store_crud.clear_delisted(session, domain, relisted)

    delisted: set[int] = set()
    if outcome.catalog_complete:
        delisted = listed_ids - seen_ids
        store_crud.mark_delisted(session, domain, delisted, at=now)

    price_rows = _price_rows_to_write(session, domain, outcome.products, now=now)
    store_crud.insert_price_observations(session, price_rows)

    ranks_written = 0
    if outcome.rank_ok and outcome.rank_handles:
        handle_to_id = {p.handle: p.product_id for p in outcome.products}
        pairs = [
            (rank, handle_to_id[handle])
            for rank, handle in enumerate(outcome.rank_handles, start=1)
            if handle in handle_to_id
        ]
        store_crud.replace_ranks(session, domain, captured_on=now.date(), pairs=pairs)
        ranks_written = len(pairs)

    if outcome.meta is not None:
        store_crud.update_store_meta(session, domain, outcome.meta)

    store_crud.update_store_status(
        session,
        domain,
        status=outcome.status,
        catalog_complete=outcome.catalog_complete,
        products_json_ok=outcome.products_json_ok,
        rank_ok=outcome.rank_ok,
        error=outcome.error,
        failed=outcome.status in (FETCH_FAILED, FETCH_NO_CATALOG_API),
    )

    return RefreshSummary(
        domain=domain,
        status=outcome.status,
        # 首刷那批入库但不算「上新」，故这里报 0——报实际数量会让日志与上新流对不上。
        new_products=0 if is_first_fetch else len(new_ids),
        price_changes=len(price_rows),
        ranks_written=ranks_written,
        delisted=len(delisted),
        relisted=len(relisted),
        catalog_complete=outcome.catalog_complete,
        rank_ok=outcome.rank_ok,
    )


def _price_rows_to_write(
    session: Session, domain: str, products: tuple[ParsedProduct, ...], *, now: datetime
) -> list[dict]:
    before = store_crud.latest_prices(session, domain)
    rows: list[dict] = []
    for product in products:
        for variant in product.variants:
            if not _price_changed(before.get(variant.variant_id), variant):
                continue
            rows.append(
                {
                    "domain": domain,
                    "variant_id": variant.variant_id,
                    "observed_at": now,
                    "product_id": product.product_id,
                    "sku": variant.sku,
                    "price": variant.price,
                    "compare_at_price": variant.compare_at_price,
                    "available": variant.available,
                }
            )
    return rows


def _claim_due_store(interval_hours: int, now: datetime) -> tuple[str, bool] | None:
    """认领一个到期站点，返回 (域名, 是否首刷)。自建会话，事务在本函数内闭合。"""
    with SessionFactory() as session:
        domain = store_crud.claim_due_store(session, interval_hours=interval_hours, now=now)
        if domain is None:
            session.rollback()
            return None
        is_first = not store_crud.existing_product_ids(session, domain)
        session.commit()
        return domain, is_first


def _persist(domain: str, outcome: StoreFetchOutcome, now: datetime, is_first: bool):
    with SessionFactory() as session:
        summary = apply_fetch_result(
            session, domain, outcome, now=now, is_first_fetch=is_first
        )
        session.commit()
        return summary


async def refresh_due_store() -> RefreshSummary | None:
    """认领并拉取一个到期站点。没有到期站点时返回 None。

    **网络那段直接 await 在事件循环上，只有数据库写入走 ``run_in_threadpool``。**
    那 32 条线程（``config.THREAD_POOL_LIMIT``）由 CSV 导出、健康检查探活、bcrypt 密码
    校验与同步后台导入共用；一家店抓 8 秒就占住一条线程 8 秒，站点数涨上去会实实在在
    地拖慢登录。旁边 ``import_queue`` 的导入接手走线程池，是因为导入本身同步阻塞。
    """
    now = datetime.now(UTC)
    claimed = await run_in_threadpool(_claim_due_store, settings.DTC_FETCH_INTERVAL_HOURS, now)
    if claimed is None:
        return None
    domain, is_first = claimed

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20.0), follow_redirects=True, headers={"User-Agent": _USER_AGENT}
    ) as client:
        outcome = await fetch_store(
            client,
            domain,
            max_pages=settings.DTC_MAX_PAGES,
            rank_top_n=settings.DTC_RANK_TOP_N,
            delay_ms=settings.DTC_REQUEST_DELAY_MS,
        )

    summary = await run_in_threadpool(_persist, domain, outcome, now, is_first)
    if summary.status != FETCH_OK:
        logger.warning(
            "独立站 %s 拉取状态 %s：%s", domain, summary.status, outcome.error or "（无详情）"
        )
    return summary
