"""ASIN 上报的业务逻辑：授权、幂等、写入顺序、事务边界。

一次上报只针对一个 `(seller_id, site)`，整段落在**同一个事务**里：授权 → 幂等判定 →
写上报记录 → 写在售关系 → 回填新增数 → 落基线。任何一步抛异常，整批不留痕。

三条不可调换的顺序：

1. **授权在最前。** 卖家不在上报者本人的关注清单里就整个请求拒绝——在售数据是共享层，
   一个人污染、全体看见。
2. **上报记录先于在售关系。** 后者的 `first_report_id` 指向前者。
3. **基线与本批 ASIN 必须落在同一个事务里。** Postgres 的 `now()` 取的是**事务开始时刻**
   而非语句时刻，所以基线与本批的 `first_seen_at` **严格相等**；而新发现的判据是
   `first_seen_at > baseline_at`（**严格大于**），首批 ASIN 因此恒不进上新流——这正是
   "基线批次不算新发现"能成立的机制。两条推论：判据改成 `>=` 会让每个卖家的首批商品整批
   涌进上新流；把基线拆到另一个事务里落，两个时刻不再相等，同样会漏进来。
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import (
    CODE_COMPETITOR_IDEMPOTENCY_KEY_REUSED,
    CODE_COMPETITOR_REPORT_EMPTY,
    CODE_COMPETITOR_SELLER_NOT_WATCHED,
    CODE_COMPETITOR_SITE_INVALID,
    ConflictError,
    UnprocessableError,
)
from app.site_registry import UnknownSiteError, normalize_site_code
from app.table.competitor import asin_crud as crud
from app.table.competitor.asin_schemas import (
    ASIN_RE,
    MAX_IMAGE_URL_LEN,
    MAX_PAGE_CONTEXT_LEN,
    MAX_TITLE_LEN,
    AsinReportRequest,
    AsinReportResponse,
    CollectorConfigResponse,
    RejectedItem,
    truncate,
)
from app.table.competitor.collector_selectors import load_selector_overrides
from app.table.competitor.seller_model import MAX_SELLER_NAME_LEN
from config import settings

logger = logging.getLogger(__name__)

# 错误码在 app/common/errors.py 集中声明，这里只引用——两处各写一份字面量会分叉，
# 而分叉的表现是前端按码分支时匹配不上，没有任何地方会报错。
CODE_IDEMPOTENCY_KEY_REUSED = CODE_COMPETITOR_IDEMPOTENCY_KEY_REUSED
CODE_SELLER_NOT_WATCHED = CODE_COMPETITOR_SELLER_NOT_WATCHED
CODE_REPORT_EMPTY = CODE_COMPETITOR_REPORT_EMPTY
CODE_SITE_INVALID = CODE_COMPETITOR_SITE_INVALID


def collector_config() -> CollectorConfigResponse:
    """采集端唯一的上限来源：插件据此提前拦截，不再手工镜像（#166 D4）。

    同时下发可选的页面选择器覆盖（extension-remote-selectors）。覆盖无效时只是不下发，
    上限照常返回。
    """
    overrides = load_selector_overrides(settings.COMPETITOR_COLLECTOR_SELECTORS_FILE)
    return CollectorConfigResponse(
        max_report_items=settings.COMPETITOR_MAX_REPORT_ITEMS,
        selectors=overrides.selectors.model_dump(exclude_none=True) if overrides else None,
        selectors_version=overrides.version if overrides else None,
    )


async def submit_report(
    db: AsyncSession, user_id: int, req: AsinReportRequest
) -> AsinReportResponse:
    """受理一次上报。`user_id` 由调用方从令牌取，**绝不从请求体读**。"""
    try:
        site = normalize_site_code(req.site)
    except UnknownSiteError as exc:
        # 不转译就会被全局兜底当成 500——非法输入，不是服务器故障。
        raise UnprocessableError(str(exc), code=CODE_SITE_INVALID) from exc
    seller_id = req.seller_id.strip()
    page_context = truncate(req.page_context, MAX_PAGE_CONTEXT_LEN)

    await _assert_watched(db, user_id, seller_id, site)

    accepted_rows, rejected = _normalize_items(req)
    if not accepted_rows:
        # 空列表或全部条目因格式非法被拒绝时，MUST NOT 落定基线：首次上报若在此刻建立
        # 基线，落的是一个不含任何 ASIN 的"空基线"——下一次正常上报时,整批存量商品都
        # 满足 first_seen_at > baseline_at，被误判成新发现。整批拒绝，一条都不写，
        # 比"允许空报但跳过落基线"更简单也更彻底：即便基线已经落定，一次零有效条目的
        # 上报本身也不携带任何可用信息，没有受理它的理由。
        message = (
            f"本次上报的 {len(req.items)} 条 ASIN 全部格式非法，未受理"
            if req.items
            else "本次上报未包含任何 ASIN，未受理"
        )
        raise UnprocessableError(message, code=CODE_REPORT_EMPTY)
    asins = [row["asin"] for row in accepted_rows]

    request_hash = crud.compute_request_hash(
        seller_id=seller_id,
        site=site,
        asins=asins,
        captured_at=req.captured_at,
        ship_to_region=req.ship_to_region,
        page_context=page_context,
    )

    report_id = (
        await db.execute(
            crud.insert_report_stmt(
                user_id=user_id,
                request_id=req.request_id,
                request_hash=request_hash,
                seller_id=seller_id,
                site=site,
                captured_at=req.captured_at,
                ship_to_region=req.ship_to_region,
                page_context=page_context,
                item_count=len(accepted_rows),
            )
        )
    ).scalar_one_or_none()

    if report_id is None:
        # 幂等键已用过。是重放还是键被复用，全看摘要——见 _replay_or_conflict。
        return await _replay_or_conflict(db, user_id, req.request_id, request_hash, rejected)

    for row in accepted_rows:
        row["first_report_id"] = report_id

    # accepted_rows 此刻必非空——空批次已在函数开头整批拒绝、从未走到这里。
    new_asins = list((await db.execute(crud.insert_asins_stmt(accepted_rows))).scalars().all())
    if new_asins:
        await db.execute(crud.update_report_new_count_stmt(report_id, len(new_asins)))

    baseline_established = (
        await db.execute(crud.set_baseline_stmt(seller_id, site))
    ).scalar_one_or_none() is not None

    # 店名只在这条非重放路径上更新：重放（上面 report_id 为空那支）MUST NOT 改共享行，
    # 没带店名的上报也不清空已有值（#166 D5）。
    seller_name = truncate((req.seller_name or "").strip() or None, MAX_SELLER_NAME_LEN)
    if seller_name:
        await db.execute(crud.update_seller_name_stmt(seller_id, site, seller_name))

    await db.commit()
    return AsinReportResponse(
        report_id=report_id,
        accepted=len(accepted_rows),
        new_asins=len(new_asins),
        baseline_established=baseline_established,
        duplicate_of=None,
        rejected=rejected,
    )


async def _assert_watched(db: AsyncSession, user_id: int, seller_id: str, site: str) -> None:
    watched = (
        await db.execute(crud.watched_seller_stmt(user_id, seller_id, site))
    ).scalar_one_or_none()
    if watched is None:
        # 整个请求拒绝，不是逐条——一次上报只针对一个卖家，这里没有"逐条"可言。
        raise UnprocessableError(
            f"卖家 {seller_id}（{site}）不在你的关注清单里，请先关注再上报",
            code=CODE_SELLER_NOT_WATCHED,
        )


def _normalize_items(req: AsinReportRequest) -> tuple[list[dict], list[RejectedItem]]:
    """规范化、去重、逐条校验。

    **同一请求内重复的 ASIN 去重、不拒绝**（同一个品出现在页面两处是采集端的常态）；
    **格式非法的逐条拒绝、其余照写**，原因回传能让采集端作者发现选择器取错了元素。
    """
    rows: list[dict] = []
    rejected: list[RejectedItem] = []
    seen: set[str] = set()

    for item in req.items:
        asin = crud.normalize_asin(item.asin)
        if not asin:
            rejected.append(RejectedItem(asin=item.asin, reason="asin_empty"))
            continue
        if not ASIN_RE.match(asin):
            rejected.append(RejectedItem(asin=item.asin, reason="asin_invalid"))
            continue
        if asin in seen:
            continue
        seen.add(asin)
        rows.append(
            {
                "seller_id": req.seller_id.strip(),
                "site": normalize_site_code(req.site),
                "asin": asin,
                "title": truncate(item.title, MAX_TITLE_LEN),
                "image_url": truncate(item.image_url, MAX_IMAGE_URL_LEN),
            }
        )
    return rows, rejected


async def _replay_or_conflict(
    db: AsyncSession,
    user_id: int,
    request_id: str,
    request_hash: str,
    rejected: list[RejectedItem],
) -> AsinReportResponse:
    """幂等键命中后的两条分支。

    **摘要一致 → 回放**，返回 200 与原记录的计数。不新增记录、不推进「最近采集于」——
    后者正是那一列存在的意义：数据看起来比实际新，是这条路最容易造出的假象。
    命中重复时 MUST NOT 返错误码，采集端收到失败的正常反应是继续重试，而这次重试本就是
    它要处理的那个失败。

    **摘要不一致 → 409**，绝不吞掉这次观测。键只能认出"这个键来过"，认不出"这两次是不是
    同一次上报"；采集端若复用了键去报另一个卖家或另一批 ASIN，静默回放就等于把一次真实
    观测丢掉，而且可能把另一个卖家的旧计数返回给它。
    """
    row = (await db.execute(crud.existing_report_stmt(user_id, request_id))).one_or_none()
    if row is None:
        # 并发下另一个事务刚插入又回滚，或记录被删。当作冲突而非静默成功。
        raise ConflictError(
            "上报未能受理，请换一个 request_id 重试",
            code=CODE_IDEMPOTENCY_KEY_REUSED,
            log_detail=f"request_id={request_id} 冲突后回查为空（user_id={user_id}）",
        )

    if row.request_hash != request_hash:
        raise ConflictError(
            "这个 request_id 已经用于另一次上报了。若这是一次新的采集，请生成新的 request_id",
            code=CODE_IDEMPOTENCY_KEY_REUSED,
            log_detail=(
                f"request_id={request_id} 被复用（user_id={user_id}）："
                f"已存摘要 {row.request_hash[:12]}… 与本次 {request_hash[:12]}… 不一致"
            ),
        )

    return AsinReportResponse(
        report_id=row.report_id,
        accepted=row.item_count,
        new_asins=row.new_count,
        baseline_established=False,
        duplicate_of=row.report_id,
        rejected=rejected,
    )
