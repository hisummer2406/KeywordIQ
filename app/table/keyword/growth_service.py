"""持续增长榜的业务层：作用域解析、缺期计算、分页与走势补齐。事务边界在这一层。

**这一层不算榜单**，只读取导入时算定的结果（见 ``importer.load_growth``）。口径的唯一
定义在那里；这里若再写一遍判定条件，两处漂移的表现是「界面显示的行不满足界面自己写的
口径」，而没有任何地方会报错。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import CODE_KEYWORD_SCOPE_INVALID, UnprocessableError
from app.site_registry import UnknownSiteError, normalize_site_code
from app.table.keyword import growth_crud as crud
from app.table.keyword.growth_schemas import (
    GrowthRowItem,
    GrowthScopeInfo,
    GrowthSearchRequest,
    GrowthSearchResponse,
)
from config import settings

logger = logging.getLogger(__name__)


class GrowthScopeError(UnprocessableError):
    """作用域参数非法（站点未登记、日期无数据/越界）→ 422。

    独立于分析屏的 `ScopeError`：错误码按域划分，关键词工具的失败不该报成 `analysis.*`
    ——排障时按码筛日志会把两条完全不同的路径混在一起。
    """

    code = CODE_KEYWORD_SCOPE_INVALID
    message = "查询作用域不合法"


def _resolve_site(site: str | None) -> str:
    """未传取默认站点；传了一律校验，非法即报错，**绝不静默回退**。"""
    if site is None:
        return settings.default_site
    try:
        return normalize_site_code(site)
    except UnknownSiteError as exc:
        raise GrowthScopeError(str(exc)) from exc


def _resolve_date(given: date | None, available: list[date]) -> date | None:
    """未传取最新可用；传了必须在可选集内，否则报错不回退。"""
    if given is None:
        return available[0] if available else None
    if given not in available:
        raise GrowthScopeError(f"日期 {given} 无数据或已移出保留窗口")
    return given


async def _available_dates(db: AsyncSession, site: str) -> list[date]:
    return list((await db.execute(crud.available_dates_stmt(site))).scalars().all())


async def _missing_dates(db: AsyncSession, site: str, on: date) -> list[date]:
    """窗口内没有数据的期次。

    缺期时榜单据实为空（所有词都凑不满窗口），这不是缺陷；但**必须让用户看见**，否则
    「今天没有增长词」与「有两天的数据没导进来」在界面上完全一样。
    """
    window_days = settings.GROWTH_WINDOW_DAYS
    present = set(
        (await db.execute(crud.window_present_dates_stmt(site, on, window_days))).scalars().all()
    )
    start = crud.window_start(on, window_days)
    return [
        d for i in range(window_days)
        if (d := start + timedelta(days=i)) not in present
    ]


async def get_scope(db: AsyncSession, site: str | None) -> GrowthScopeInfo:
    """作用域元信息 + 判定口径（日期选择器与口径说明条用）。"""
    resolved_site = _resolve_site(site)
    dates = await _available_dates(db, resolved_site)
    return GrowthScopeInfo(
        site=resolved_site,
        latest_date=dates[0] if dates else None,
        available_dates=dates,
        window_days=settings.GROWTH_WINDOW_DAYS,
        min_rank_gain=settings.GROWTH_MIN_RANK_GAIN,
        min_daily_rate=settings.GROWTH_MIN_DAILY_RATE,
    )


async def search(db: AsyncSession, req: GrowthSearchRequest) -> GrowthSearchResponse:
    """一页榜单 + 走势 + 缺期回显。"""
    site = _resolve_site(req.site)
    dates = await _available_dates(db, site)
    on = _resolve_date(req.date, dates)

    empty = GrowthSearchResponse(
        site=site, date=on, window_days=settings.GROWTH_WINDOW_DAYS
    )
    if on is None:
        # 该站点一期日报都没有。空结果而非报错——新站点接入初期就是这个状态。
        return empty

    missing = await _missing_dates(db, site, on)
    empty.missing_dates = missing

    total = (await db.execute(crud.count_stmt(site, on))).scalar_one()
    if not total:
        return empty

    rows = (
        await db.execute(
            crud.list_stmt(
                site, on,
                order_column=crud.SORTABLE_COLUMNS[req.orderBy],
                descending=req.orderDir == "desc",
                offset=(req.page - 1) * req.perPage,
                limit=req.perPage,
            )
        )
    ).all()

    # 先分页再批量补附加：走势只查当页这几十个词，不随总行数增长。
    items = [
        GrowthRowItem(
            keyword_id=r.keyword_id,
            keyword=r.keyword,
            start_ranking=r.start_ranking,
            end_ranking=r.end_ranking,
            rank_gain=r.rank_gain,
            min_growth_rate=float(r.min_growth_rate),
        )
        for r in rows
    ]
    if items:
        keyword_ids = [it.keyword_id for it in items]
        trend_rows = (
            await db.execute(
                crud.trends_stmt(site, keyword_ids, on, settings.GROWTH_WINDOW_DAYS)
            )
        ).all()
        ranks, trend_dates = crud.shape_trends(trend_rows)
        for it in items:
            it.trend = ranks.get(it.keyword_id, [])
            it.trend_dates = trend_dates.get(it.keyword_id, [])

    return GrowthSearchResponse(
        data=items, total=total, site=site, date=on,
        missing_dates=missing, window_days=settings.GROWTH_WINDOW_DAYS,
    )
