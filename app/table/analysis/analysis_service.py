"""站点作用域查询的业务逻辑层。

职责：解析作用域三元组 (site, view, date)——默认站点、站点校验、日期默认/校验，
再调 crud 组装 ProTable 响应并回显作用域。事务边界在此，crud 不提交。
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from config import settings
from app.site_registry import UnknownSiteError, normalize_site_code
from app.table.analysis import analysis_crud as crud
from app.table.search.search_schemas import (
    AnalysisSearchRequest,
    AnalysisSearchResponse,
    CategoryOption,
    DayRowItem,
    ScopeInfo,
    WeekRowItem,
)

logger = logging.getLogger(__name__)


class ScopeError(ValueError):
    """作用域参数非法（站点未登记、日期无数据/越界）。API 层映射为 422。"""


def _resolve_site(site: Optional[str]) -> str:
    """未传取默认站点；一律校验合法，非法即报错，绝不静默回退到默认。"""
    if site is None:
        return settings.default_site
    try:
        return normalize_site_code(site)
    except UnknownSiteError as exc:
        raise ScopeError(str(exc)) from exc


def _resolve_date(
    given: Optional[date], available: list[date]
) -> Optional[date]:
    """日期解析：未传取最新可用；传了必须在可选集内，否则报错不回退。"""
    if given is None:
        return available[0] if available else None
    if given not in available:
        raise ScopeError(f"日期 {given} 无数据或已移出保留窗口")
    return given


def get_scope(db: Session, site: Optional[str], view: str) -> ScopeInfo:
    """作用域元信息：可选日期集 + 最新日期（日期选择器用）。"""
    resolved_site = _resolve_site(site)
    dates = crud.available_dates(db, resolved_site, view)
    return ScopeInfo(
        site=resolved_site,
        view=view,  # type: ignore[arg-type]
        latest_date=dates[0] if dates else None,
        available_dates=dates,
    )


def search(db: Session, req: AnalysisSearchRequest) -> AnalysisSearchResponse:
    """作用域内分页查询，返回 ProTable 契约 + 生效作用域回显。"""
    site = _resolve_site(req.site)
    view = req.view
    dates = crud.available_dates(db, site, view)
    on = _resolve_date(req.date, dates)

    # 该站点该视图无任何数据：正常返回空结果（非报错）
    if on is None:
        return AnalysisSearchResponse(success=True, data=[], total=0, site=site, view=view, date=None)

    rows = crud.search_rows(db, site, view, on, req)

    if crud.request_has_filters(req):
        total = crud.count_filtered(db, site, view, on, req)
    else:
        total = crud.scope_row_count(db, site, view, on)

    # 按视图构造行 DTO。日视图附带只读的近 7 天走势与当周排名变化：先分页拿本页
    # keyword_id，再批量补附加，避免在整切片上做相关子查询。附加信息不改变结果集构成。
    if view == "DAY":
        ids = [r["keyword_id"] for r in rows]
        trends = crud.fetch_trends(db, site, ids, on)
        week_changes = crud.fetch_week_changes(db, site, ids, on)
        data = []
        for r in rows:
            item = DayRowItem(**r)
            item.trend = trends.get(r["keyword_id"], [])
            item.week_change = week_changes.get(r["keyword_id"])
            data.append(item.model_dump())
    else:
        data = [WeekRowItem(**r).model_dump() for r in rows]

    return AnalysisSearchResponse(
        success=True, data=data, total=total, site=site, view=view, date=on
    )


def get_categories(db: Session, site: Optional[str], view: str) -> list[CategoryOption]:
    """类目下拉选项（按作用域）。"""
    resolved_site = _resolve_site(site)
    return [CategoryOption(**opt) for opt in crud.category_options(db, resolved_site, view)]
