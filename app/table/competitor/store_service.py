"""独立站监控的业务层：差分、完整性判定、状态回写、拉取编排。

**事务边界在这一层**，crud 层永不 commit。

拉取的执行形态见 ``refresh_due_store``：网络那段 ``await`` 在事件循环上，只有数据库
写入走 ``run_in_threadpool``。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.common.errors import BadRequestError, ConflictError, NotFoundError, UnprocessableError
from app.table.competitor import store_crud
from app.table.competitor.store_fetcher import (
    FETCH_FAILED,
    FETCH_NO_CATALOG_API,
    FETCH_OK,
    MetaParseError,
    ParsedProduct,
    ParsedStoreMeta,
    StoreFetchOutcome,
    fetch_store,
    normalize_store_domain,
    probe_store_meta,
)
from app.table.competitor.store_model import (
    CompetitorStore,
    CompetitorStoreProduct,
    UserStoreWatch,
)
from app.table.competitor.store_schemas import (
    STATUS_FAILED,
    STATUS_INCOMPLETE,
    STATUS_NEVER_FETCHED,
    STATUS_NO_CATALOG_API,
    STATUS_OK,
    BatchAddedItem,
    BatchRejectedItem,
    NewArrivalOut,
    NewArrivalRequest,
    NewArrivalResponse,
    PricePointOut,
    ProductHistoryResponse,
    ProductListRequest,
    ProductListResponse,
    ProductOut,
    RankPointOut,
    StoreAddRequest,
    StoreBatchAddRequest,
    StoreBatchAddResponse,
    StoreListRequest,
    StoreListResponse,
    StoreOut,
    StoreUpdateRequest,
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
        # 首刷时落定 first_fetch_at——上新流靠它把首刷那批排除在外。
        first_fetch_at=now if is_first_fetch else None,
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


# ---------------------------------------------------------------------------
# 请求路径（异步）
#
# 与上面的后台写入路径分开：那边是批量写、用同步会话；这边是并发读、用异步会话。
# 私有性由每条查询里的 ``user_id`` 保证，不靠界面不展示；取不到自己的东西一律 404
# 而非 403（403 等于确认了那个 id 存在）。
# ---------------------------------------------------------------------------

STORE_NOT_WATCHED = "没有找到该独立站"
STORE_ALREADY_WATCHED = "已经关注过这个独立站了"
STORE_NOT_SHOPIFY = "这个站点不是 Shopify 建站，暂时无法监控"
STORE_UNREACHABLE = "无法访问该站点，请确认域名是否正确、站点是否可以打开"


def _store_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(15.0), follow_redirects=True, headers={"User-Agent": _USER_AGENT}
    )


async def _probe_shopify(domain: str) -> ParsedStoreMeta:
    """加入前先探一次 ``/meta.json``。

    **在加入这一步就判定，不留到第一次拉取。** 非 Shopify 站点如果先建了行、等一天后
    才在拉取里失败，用户看到的是「抓取失败」，而真相是这个站点根本不受支持——他会去
    重试、去等，而不是去换一个站点。
    """
    async with _store_client() as client:
        try:
            return await probe_store_meta(client, domain)
        except (MetaParseError, httpx.HTTPStatusError) as exc:
            # **HTTPStatusError 归到「不是 Shopify」而不是「打不开」**：服务器答复了，
            # 只是那个路径上没有 meta——最典型的就是任何非 Shopify 站点对 /meta.json
            # 返回 404。归进「无法访问」的表现是用户对着一个好好的站点反复重试。
            raise BadRequestError(
                STORE_NOT_SHOPIFY, log_detail=f"{domain} 不是 Shopify：{exc}"
            ) from exc
        except httpx.HTTPError as exc:
            # 到这里才是真的够不着：超时、DNS 解析不了、连接被拒。
            raise BadRequestError(
                STORE_UNREACHABLE, log_detail=f"{domain} meta 探测失败：{exc}"
            ) from exc


async def add_store(db: AsyncSession, user_id: int, req: StoreAddRequest) -> StoreOut:
    """加入一个独立站并关注。域名由服务端规范化，非 Shopify 显式拒绝。"""
    domain = normalize_store_domain(req.domain)

    already = await db.scalar(
        select(UserStoreWatch.watch_id).where(
            UserStoreWatch.user_id == user_id, UserStoreWatch.domain == domain
        )
    )
    if already is not None:
        raise ConflictError(STORE_ALREADY_WATCHED)

    watched = await db.scalar(
        select(func.count()).select_from(UserStoreWatch).where(UserStoreWatch.user_id == user_id)
    )
    # 沿用竞对卖家那一项上限，**按类型各自计算**，不新增配置项——多一个旋钮就多一处
    # 迟早与文档不一致的地方，而这两类关注的抓取代价本来也不同。
    if watched >= settings.COMPETITOR_MAX_WATCH_PER_USER:
        raise UnprocessableError(
            f"关注的独立站已达上限 {settings.COMPETITOR_MAX_WATCH_PER_USER} 个"
        )

    meta = await _probe_shopify(domain)
    await db.execute(
        pg_insert(CompetitorStore)
        .values(
            domain=domain,
            platform="SHOPIFY",
            name=meta.name,
            currency=meta.currency,
            country=meta.country,
            shop_id=meta.shop_id,
            myshopify_domain=meta.myshopify_domain,
            ships_to_countries=list(meta.ships_to_countries) or None,
        )
        .on_conflict_do_nothing(index_elements=["domain"])
    )
    await db.execute(
        pg_insert(UserStoreWatch).values(
            user_id=user_id, domain=domain, group_id=req.group_id, note=req.note
        )
    )
    await db.commit()

    listing = await list_stores(db, user_id, StoreListRequest(per_page=100))
    for row in listing.data:
        if row.domain == domain:
            return row
    raise NotFoundError(STORE_NOT_WATCHED)  # pragma: no cover - 刚写入必然存在


# 并发探测的上限。串行几十条是分钟级、必然超时；无上限并发对着几十个不同站点同时发
# 请求，既容易被当成异常流量，也会让本机连接数失控。8 条并发下，200 条的最坏耗时约
# 25 秒，仍在一次请求可承受的范围内——这正是本能力不需要异步任务的原因。
_BATCH_PROBE_CONCURRENCY = 8


async def add_stores_batch(
    db: AsyncSession, user_id: int, req: StoreBatchAddRequest
) -> StoreBatchAddResponse:
    """一次加入多个独立站，**一步完成**，返回逐条结果。

    不做预览 + 确认两步流。判据是这个操作可不可逆：卖家侧那套存在的理由是
    ``replace_group`` 会清空整组重建、且 2657 条来自书签文件用户不知道里面有什么；
    这里两条都不成立——用户粘的是自己知道的域名，加错了取关一次点击且数据还留着。

    结果按**输入行**组织而不是按站点：被拒绝的条目根本得不到域名，只能用原文回指。
    """
    rejected: list[BatchRejectedItem] = []

    # 逐行规范化。复用单条路径那一份，**不另写**——两份分叉的表现是「单个粘贴能进，
    # 放进批量里同样的链接却被拒」。
    normalized: dict[str, str] = {}  # 规范化域名 -> 首次出现的原文
    for raw in req.domains:
        if not raw or not raw.strip():
            continue
        try:
            domain = normalize_store_domain(raw)
        except BadRequestError as exc:
            rejected.append(BatchRejectedItem(input=raw, reason=exc.message))
            continue
        normalized.setdefault(domain, raw)  # 同批重复只处理一次

    if not normalized:
        return StoreBatchAddResponse(added=[], already_watched=[], rejected=rejected)

    watched_now = set(
        (
            await db.execute(
                select(UserStoreWatch.domain).where(
                    UserStoreWatch.user_id == user_id,
                    UserStoreWatch.domain.in_(normalized.keys()),
                )
            )
        )
        .scalars()
        .all()
    )

    watched_count = await db.scalar(
        select(func.count()).select_from(UserStoreWatch).where(UserStoreWatch.user_id == user_id)
    )
    to_add = [d for d in normalized if d not in watched_now]
    # 超限**整批拒绝不截断**——截断会让用户以为全部加进去了。
    if watched_count + len(to_add) > settings.COMPETITOR_MAX_WATCH_PER_USER:
        raise UnprocessableError(
            f"这批会让关注的独立站超过上限 {settings.COMPETITOR_MAX_WATCH_PER_USER} 个，"
            f"已关注 {watched_count} 个、这批新增 {len(to_add)} 个。整批未写入。"
        )

    probed = await _probe_many(to_add)

    added: list[BatchAddedItem] = []
    for domain in to_add:
        raw = normalized[domain]
        outcome = probed[domain]
        if isinstance(outcome, str):  # 拒绝原因
            rejected.append(BatchRejectedItem(input=raw, reason=outcome))
            continue
        await db.execute(
            pg_insert(CompetitorStore)
            .values(
                domain=domain,
                platform="SHOPIFY",
                name=outcome.name,
                currency=outcome.currency,
                country=outcome.country,
                shop_id=outcome.shop_id,
                myshopify_domain=outcome.myshopify_domain,
                ships_to_countries=list(outcome.ships_to_countries) or None,
            )
            .on_conflict_do_nothing(index_elements=["domain"])
        )
        await db.execute(
            pg_insert(UserStoreWatch).values(
                user_id=user_id, domain=domain, group_id=req.group_id, note=None
            )
        )
        added.append(
            BatchAddedItem(
                input=raw, domain=domain, name=outcome.name, currency=outcome.currency
            )
        )

    await db.commit()

    existing_rows = [
        BatchAddedItem(input=normalized[d], domain=d, name=None, currency=None)
        for d in normalized
        if d in watched_now
    ]
    return StoreBatchAddResponse(
        added=added, already_watched=existing_rows, rejected=rejected
    )


async def _probe_many(domains: list[str]) -> dict[str, ParsedStoreMeta | str]:
    """并发探测一批域名。返回 meta 或**拒绝原因字符串**。

    所有条目共用同一个 ``AsyncClient``（连接池复用），并发数由信号量限死。
    """
    if not domains:
        return {}
    semaphore = asyncio.Semaphore(_BATCH_PROBE_CONCURRENCY)

    async with _store_client() as client:

        async def _one(domain: str) -> tuple[str, ParsedStoreMeta | str]:
            async with semaphore:
                try:
                    return domain, await probe_store_meta(client, domain)
                except (MetaParseError, httpx.HTTPStatusError):
                    # 服务器答复了、只是没有 meta = 不是 Shopify。与单条路径同一分类，
                    # 两边分叉会让同一个站点在两条入口得到不同的拒绝理由。
                    return domain, STORE_NOT_SHOPIFY
                except httpx.HTTPError:
                    return domain, STORE_UNREACHABLE

        results = await asyncio.gather(*(_one(d) for d in domains))
    return dict(results)


async def list_stores(
    db: AsyncSession, user_id: int, req: StoreListRequest
) -> StoreListResponse:
    stmt = store_crud.watched_stores_stmt(
        user_id, group_id=req.group_id, ungrouped_only=req.ungrouped_only
    )
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        await db.execute(
            stmt.order_by(CompetitorStore.domain)
            .offset((req.page - 1) * req.per_page)
            .limit(req.per_page)
        )
    ).all()

    data = [
        StoreOut(
            domain=row[0].domain,
            name=row[0].name,
            currency=row[0].currency,
            country=row[0].country,
            group_id=row.group_id,
            group_name=row.group_name,
            note=row.note,
            watched_at=row.watched_at,
            status=_display_status(row[0]),
            catalog_complete=row[0].catalog_complete,
            rank_ok=row[0].rank_ok,
            consecutive_failures=row[0].consecutive_failures,
            last_fetch_at=row[0].last_fetch_at,
            first_fetch_at=row[0].first_fetch_at,
            product_count=row.product_count,
            new_in_7d=row.new_in_7d,
        )
        for row in rows
    ]
    return StoreListResponse(data=data, total=total)


def _display_status(store: CompetitorStore) -> str:
    """站点状态。**三类失败绝不合并**——见 spec「三类失败状态分开呈现」。

    「从未拉取」是第四种，必须与「拉取失败」分开：新加的站点还没到点，不是坏了。
    """
    if store.last_fetch_at is None:
        return STATUS_NEVER_FETCHED
    if not store.products_json_ok:
        return STATUS_NO_CATALOG_API
    if store.last_fetch_status == FETCH_FAILED:
        return STATUS_FAILED
    if not store.catalog_complete:
        return STATUS_INCOMPLETE
    return STATUS_OK


async def _require_watched(db: AsyncSession, user_id: int, domain: str) -> None:
    """归属校验。别人的（或不存在的）一律 404 而非 403——403 等于确认了它存在。"""
    found = await db.scalar(
        select(UserStoreWatch.watch_id).where(
            UserStoreWatch.user_id == user_id, UserStoreWatch.domain == domain
        )
    )
    if found is None:
        raise NotFoundError(STORE_NOT_WATCHED)


async def update_store_watch(
    db: AsyncSession, user_id: int, domain: str, req: StoreUpdateRequest
) -> None:
    await _require_watched(db, user_id, domain)
    values: dict[str, object] = {}
    if req.clear_group:
        values["group_id"] = None
    elif req.group_id is not None:
        values["group_id"] = req.group_id
    if req.note is not None:
        values["note"] = req.note
    if not values:
        return
    await db.execute(
        update(UserStoreWatch)
        .where(UserStoreWatch.user_id == user_id, UserStoreWatch.domain == domain)
        .values(**values)
    )
    await db.commit()


async def remove_store_watch(db: AsyncSession, user_id: int, domain: str) -> None:
    """取消关注。

    只删关注关系，**站点主体与已抓到的数据都留着**——别人可能还在盯同一个站点，
    而重新关注时历史还在。这与 Amazon 侧取关的处置一致。
    """
    await _require_watched(db, user_id, domain)
    await db.execute(
        delete(UserStoreWatch).where(
            UserStoreWatch.user_id == user_id, UserStoreWatch.domain == domain
        )
    )
    await db.commit()


async def list_products(
    db: AsyncSession, user_id: int, req: ProductListRequest
) -> ProductListResponse:
    domain = normalize_store_domain(req.domain)
    await _require_watched(db, user_id, domain)

    stmt = store_crud.products_stmt(domain, include=req.include, new_only=req.new_only)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        await db.execute(
            stmt.order_by(CompetitorStoreProduct.published_at.desc().nullslast())
            .offset((req.page - 1) * req.per_page)
            .limit(req.per_page)
        )
    ).all()
    currency = await db.scalar(
        select(CompetitorStore.currency).where(CompetitorStore.domain == domain)
    )

    return ProductListResponse(
        data=[
            ProductOut(
                product_id=row[0].product_id,
                handle=row[0].handle,
                title=row[0].title,
                vendor=row[0].vendor,
                product_type=row[0].product_type,
                image_url=row[0].image_url,
                published_at=row[0].published_at,
                first_seen_at=row[0].first_seen_at,
                delisted_at=row[0].delisted_at,
                current_price=row.price,
                compare_at_price=row.compare_at_price,
                available=row.available,
                latest_rank=row.latest_rank,
            )
            for row in rows
        ],
        total=total,
        currency=currency,
    )


async def new_arrivals(
    db: AsyncSession, user_id: int, req: NewArrivalRequest
) -> NewArrivalResponse:
    """上新流。

    当前只有独立站一类来源（Amazon 侧的采集不在本变更范围），但**响应形状已按两类来源
    设计**：每行带 ``source`` 与 ``time_kind``，独立站是 ``PUBLISHED``（上架于），
    Amazon 接入时是 ``DISCOVERED``（发现于）。把后者标成「上架于」等于拿「我们发现得晚」
    冒充「他上得晚」。
    """
    stmt = store_crud.new_arrivals_stmt(user_id, days=req.days)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        await db.execute(stmt.offset((req.page - 1) * req.per_page).limit(req.per_page))
    ).all()

    return NewArrivalResponse(
        data=[
            NewArrivalOut(
                source="DTC",
                source_key=row[0].domain,
                source_name=row.store_name,
                product_key=str(row[0].product_id),
                title=row[0].title,
                image_url=row[0].image_url,
                # 独立站有数据源给的真实上架时间，故报 published_at；缺失才退回首次见到。
                at=row[0].published_at or row[0].first_seen_at,
                time_kind="PUBLISHED" if row[0].published_at else "DISCOVERED",
            )
            for row in rows
        ],
        total=total,
    )


async def product_history(
    db: AsyncSession, user_id: int, domain_raw: str, product_id: int
) -> ProductHistoryResponse:
    domain = normalize_store_domain(domain_raw)
    await _require_watched(db, user_id, domain)

    prices = (await db.execute(store_crud.price_history_stmt(domain, product_id))).scalars().all()
    ranks = (await db.execute(store_crud.rank_history_stmt(domain, product_id))).all()
    currency = await db.scalar(
        select(CompetitorStore.currency).where(CompetitorStore.domain == domain)
    )

    return ProductHistoryResponse(
        domain=domain,
        product_id=product_id,
        currency=currency,
        prices=[
            PricePointOut(
                observed_at=p.observed_at,
                variant_id=p.variant_id,
                sku=p.sku,
                price=p.price,
                compare_at_price=p.compare_at_price,
                available=p.available,
            )
            for p in prices
        ],
        ranks=[RankPointOut(captured_on=r.captured_on, rank=r.rank) for r in ranks],
    )
