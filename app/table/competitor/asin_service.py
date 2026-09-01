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
    CODE_COMPETITOR_SELLER_NOT_WATCHED,
    ConflictError,
    UnprocessableError,
)
from app.site_registry import normalize_site_code
from app.table.competitor import asin_crud as crud
from app.table.competitor.asin_schemas import (
    ASIN_RE,
    MAX_IMAGE_URL_LEN,
    MAX_PAGE_CONTEXT_LEN,
    MAX_TITLE_LEN,
    AsinReportRequest,
    AsinReportResponse,
    RejectedItem,
    truncate,
)

logger = logging.getLogger(__name__)

# 错误码在 app/common/errors.py 集中声明，这里只引用——两处各写一份字面量会分叉，
# 而分叉的表现是前端按码分支时匹配不上，没有任何地方会报错。
CODE_IDEMPOTENCY_KEY_REUSED = CODE_COMPETITOR_IDEMPOTENCY_KEY_REUSED
CODE_SELLER_NOT_WATCHED = CODE_COMPETITOR_SELLER_NOT_WATCHED


async def submit_report(
    db: AsyncSession, user_id: int, req: AsinReportRequest
) -> AsinReportResponse:
    """受理一次上报。`user_id` 由调用方从令牌取，**绝不从请求体读**。"""
    site = normalize_site_code(req.site)
    seller_id = req.seller_id.strip()
    page_context = truncate(req.page_context, MAX_PAGE_CONTEXT_LEN)

    await _assert_watched(db, user_id, seller_id, site)

    accepted_rows, rejected = _normalize_items(req)
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

    new_asins: list[str] = []
    if accepted_rows:
        new_asins = list(
            (await db.execute(crud.insert_asins_stmt(accepted_rows))).scalars().all()
        )
        if new_asins:
            await db.execute(crud.update_report_new_count_stmt(report_id, len(new_asins)))

    baseline_established = (
        await db.execute(crud.set_baseline_stmt(seller_id, site))
    ).scalar_one_or_none() is not None

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
