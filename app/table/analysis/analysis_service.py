"""站点作用域查询的业务逻辑层。

职责：解析作用域三元组 (site, view, date)——默认站点、站点校验、日期默认/校验，
再调 crud 组装 ProTable 响应并回显作用域。事务边界在此，crud 不提交。
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date
from typing import Iterator, Optional

from sqlalchemy.orm import Session

from config import settings
from database import SessionFactory
from app.common.errors import (
    CODE_ANALYSIS_EXPORT_TOO_LARGE,
    CODE_ANALYSIS_SCOPE_INVALID,
    PayloadTooLargeError,
    UnprocessableError,
)
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


class ScopeError(UnprocessableError):
    """作用域参数非法（站点未登记、日期无数据/越界）→ 422。

    继承 `APIError` 后由全局处理器直接产出统一错误体，API 层不再需要 try/except 转译。
    """

    code = CODE_ANALYSIS_SCOPE_INVALID
    message = "查询作用域不合法"


class ExportTooLargeError(PayloadTooLargeError):
    """导出结果超出行数上限 → 413。"""

    code = CODE_ANALYSIS_EXPORT_TOO_LARGE
    message = "导出结果超出上限"


# 导出列：与列表屏保持一致的中文表头。日视图多两列附加信息（走势/当周变化），
# 与 search 的视图差异同源。
_EXPORT_COLUMNS_BASE: list[tuple[str, str]] = [
    ("keyword", "关键词"),
    ("current_ranking", "当期排名"),
    ("previous_ranking", "上期排名"),
    ("ranking_change", "排名变化"),
    ("is_new", "是否新词"),
    ("top1_asin", "ASIN"),
    ("top1_title", "商品标题"),
    ("top1_brand", "品牌"),
    ("top1_category", "类目"),
    ("top1_click_share", "点击份额"),
    ("top1_conversion_share", "转化份额"),
]
_EXPORT_COLUMNS_DAY_EXTRA: list[tuple[str, str]] = [
    ("trend", "近7天走势"),
    ("week_change", "当周排名变化"),
]


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


def resolve_export_scope(db: Session, req: AnalysisSearchRequest) -> tuple[str, str, Optional[date]]:
    """解析导出作用域并在开始流式输出前把上限检查做掉。

    上限必须在响应头发出之前检查：一旦开始流，就只能给出 200 + 一个被截断的 CSV，
    而截断的 CSV 会被当成完整数据使用。宁可 413 让用户加筛选。
    """
    site = _resolve_site(req.site)
    dates = crud.available_dates(db, site, req.view)
    on = _resolve_date(req.date, dates)
    if on is None:
        return site, req.view, None

    cap = settings.EXPORT_ROW_CAP
    if crud.request_has_filters(req):
        count = crud.count_capped(db, site, req.view, on, req, cap + 1)
    else:
        count = crud.scope_row_count(db, site, req.view, on)
    if count > cap:
        raise ExportTooLargeError(
            f"导出结果超过 {cap} 行上限，请补充筛选条件后再导出"
        )
    return site, req.view, on


def iter_export_csv(
    site: str, view: str, on: Optional[date], req: AnalysisSearchRequest
) -> Iterator[str]:
    """产出 CSV 文本块（首块带 UTF-8 BOM）。

    自建会话而非复用请求会话：流式响应体是在依赖注入清理之后才被消费的，
    沿用请求会话会踩到"会话已关闭"。会话在生成器结束时（含客户端断开导致的
    GeneratorExit）于 finally 中关闭。

    无数据的作用域产出只有表头的 CSV——空表格比一个报错更符合"导出当前视图"的语义。
    """
    columns = _EXPORT_COLUMNS_BASE + (_EXPORT_COLUMNS_DAY_EXTRA if view == "DAY" else [])
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")

    def _flush() -> str:
        chunk = buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        return chunk

    writer.writerow([label for _, label in columns])
    yield "﻿" + _flush()

    if on is None:
        return

    db = SessionFactory()
    try:
        for rows in crud.iter_export_rows(db, site, view, on, req, settings.EXPORT_ROW_CAP):
            if view == "DAY":
                ids = [r["keyword_id"] for r in rows]
                trends = crud.fetch_trends(db, site, ids, on)
                week_changes = crud.fetch_week_changes(db, site, ids, on)
            for r in rows:
                row = dict(r)
                if view == "DAY":
                    kid = r["keyword_id"]
                    row["trend"] = " ".join(str(v) for v in trends.get(kid, []))
                    row["week_change"] = week_changes.get(kid)
                writer.writerow([_render_cell(row.get(key)) for key, _ in columns])
            yield _flush()
    finally:
        db.close()


def _render_cell(value) -> str:
    """单元格取值：布尔转中文，None 留空（不补零——空与 0 在排名变化上含义不同）。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def export_filename(site: str, view: str, on: Optional[date]) -> str:
    """导出文件名带作用域三元组，便于归档时一眼认出是哪个切片。"""
    return f"{site}_{view}_{on or 'empty'}.csv"


def get_categories(db: Session, site: Optional[str], view: str) -> list[CategoryOption]:
    """类目下拉选项（按作用域）。"""
    resolved_site = _resolve_site(site)
    return [CategoryOption(**opt) for opt in crud.category_options(db, resolved_site, view)]
