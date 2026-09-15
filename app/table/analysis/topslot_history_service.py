"""ASIN/品牌悬浮历史的业务层（issue #96）。

职责：站点/keyword_id 归属校验、身份（ASIN/品牌原文）解析、分页参数校验、历史范围
计算（保留下界到来源页面已生效的锚定日）。查询本身在 `topslot_history_crud` 里。

与 `trend_service` 的关键差异见 openspec 变更 `add-topslot-history-popover` 的
design.md 决策 4、5：这里不做五态覆盖率枚举，`on` 也没有缺省值或退化重算——它必须是
调用方（前端）已生效查询作用域的锚定日，服务层只做宽松合法性校验，不重新解析"最新一期"。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import (
    CODE_ANALYSIS_TOPSLOT_NOT_FOUND,
    CODE_ANALYSIS_TOPSLOT_PAGE_SIZE_EXCEEDED,
    CODE_ANALYSIS_TOPSLOT_SITE_MISMATCH,
    BadRequestError,
    NotFoundError,
    UnprocessableError,
)
from app.site_registry import UnknownSiteError, normalize_site_code
from app.table.analysis import topslot_history_crud as crud
from app.table.analysis.analysis_crud import fact_and_datecol
from app.table.analysis.analysis_service import ScopeError
from app.table.analysis.partitioning import retention_cutoff
from app.table.analysis.topslot_history_schemas import (
    TopslotHistoryItem,
    TopslotHistoryResponse,
    TopslotKind,
)
from config import settings

#: 每页条数上限。悬浮面板一屏能看到的量级；超限拒绝而不截断——截断会让前端算出的
#: 页数与实际不符（同 `import_batch_crud.MAX_PER_PAGE` 的既有范式）。
MAX_PAGE_SIZE = 20


class TopslotKeywordNotFoundError(NotFoundError):
    code = CODE_ANALYSIS_TOPSLOT_NOT_FOUND
    message = "关键词不存在"


class TopslotSiteMismatchError(UnprocessableError):
    code = CODE_ANALYSIS_TOPSLOT_SITE_MISMATCH
    message = "关键词不属于该站点"


class TopslotPageSizeExceededError(BadRequestError):
    code = CODE_ANALYSIS_TOPSLOT_PAGE_SIZE_EXCEEDED


def _resolve_site(site: str) -> str:
    try:
        return normalize_site_code(site)
    except UnknownSiteError as exc:
        raise ScopeError(str(exc)) from exc


async def _require_keyword_site(db: AsyncSession, keyword_id: int, site: str) -> None:
    row = (await db.execute(crud.keyword_site_stmt(keyword_id))).first()
    if row is None:
        raise TopslotKeywordNotFoundError()
    (actual_site,) = row
    if actual_site != site:
        raise TopslotSiteMismatchError(f"该关键词属于 {actual_site} 站点，与请求的 {site} 不一致")


def _validate_page(page: int, page_size: int) -> tuple[int, int]:
    if page < 1:
        raise BadRequestError(f"page 至少为 1，收到 {page}")
    if page_size < 1:
        raise BadRequestError(f"page_size 至少为 1，收到 {page_size}")
    if page_size > MAX_PAGE_SIZE:
        raise TopslotPageSizeExceededError(f"每页条数最多 {MAX_PAGE_SIZE} 条，收到 {page_size}")
    return page, page_size


async def _resolve_identity_id(db: AsyncSession, kind: TopslotKind, value: str) -> int | None:
    """身份原文 → id。查不到返回 ``None``（调用方据此短路为空结果，不报错）。

    `dim_product.asin` / `dim_brand.name` 均有唯一约束，精确匹配 O(1)，见 design.md
    决策 2："这个身份从没在这个关键词出现过"与"解析到了但零期命中"是同一种空状态。
    """
    stmt = crud.resolve_asin_stmt(value) if kind == "ASIN" else crud.resolve_brand_stmt(value)
    row = (await db.execute(stmt)).first()
    return row[0] if row else None


async def get_topslot_history(
    db: AsyncSession,
    *,
    site: str,
    view: str,
    keyword_id: int,
    kind: TopslotKind,
    value: str,
    on: date,
    page: int,
    page_size: int,
) -> TopslotHistoryResponse:
    resolved_site = _resolve_site(site)
    await _require_keyword_site(db, keyword_id, resolved_site)
    page, page_size = _validate_page(page, page_size)

    ident_id = await _resolve_identity_id(db, kind, value)
    if ident_id is None:
        return TopslotHistoryResponse(total=0, items=[])

    fact, date_col = fact_and_datecol(view)
    start = retention_cutoff(date.today(), settings.HOT_WINDOW_MONTHS)
    end = on
    offset = (page - 1) * page_size

    total = (
        await db.execute(
            crud.count_stmt(
                fact, date_col, kind, ident_id,
                site=resolved_site, keyword_id=keyword_id, start=start, end=end,
            )
        )
    ).scalar_one()

    rows = (
        await db.execute(
            crud.history_stmt(
                fact, date_col, kind, ident_id,
                site=resolved_site, keyword_id=keyword_id, start=start, end=end,
                offset=offset, limit=page_size,
            )
        )
    ).all()

    items = [TopslotHistoryItem(**row) for row in crud.shape_items(rows)]
    return TopslotHistoryResponse(total=total, items=items)
