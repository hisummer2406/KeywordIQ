"""站点作用域查询 API（V2）。瘦控制器：调 service，映射作用域错误。

- POST /api/analysis/search   分页查询，返回 ProTable 契约 + 作用域回显
- GET  /api/analysis/scope    作用域元信息（可选日期集 + 最新日期），日期选择器用
- GET  /api/analysis/categories  类目下拉选项（按作用域）
"""
import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database import get_db
from app.table.analysis import analysis_service as service
from app.table.analysis.analysis_service import ScopeError
from app.table.search.search_schemas import (
    AnalysisSearchRequest,
    AnalysisSearchResponse,
    CategoryOption,
    ScopeInfo,
)
from app.auth.simple_auth import simple_auth
from app.auth.permissions import ANALYSIS_EXPORT, ANALYSIS_VIEW

logger = logging.getLogger(__name__)

analysis_router = APIRouter()

_View = Literal["DAY", "WEEK"]


@analysis_router.post("/search", response_model=AnalysisSearchResponse)
def search_data(
    req: AnalysisSearchRequest,
    db: Session = Depends(get_db),
    _user=Depends(simple_auth.require_permission(ANALYSIS_VIEW)),
) -> AnalysisSearchResponse:
    """作用域内分页查询。失败以统一形状（success:false + message）返回。"""
    try:
        return service.search(db, req)
    except ScopeError as exc:
        return AnalysisSearchResponse(
            success=False, data=[], total=0,
            site=req.site or "", view=req.view, date=None, message=str(exc),
        )


@analysis_router.post("/export")
def export_data(
    req: AnalysisSearchRequest,
    db: Session = Depends(get_db),
    _user=Depends(simple_auth.require_permission(ANALYSIS_EXPORT)),
) -> StreamingResponse:
    """把当前作用域与筛选下的结果流式导出为 CSV。

    与 /search 同一个请求体，故导出的永远是用户当下看到的那一份数据（分页参数忽略）。
    超出行数上限 → 413（不截断）；非法作用域 → 422。
    """
    try:
        site, view, on = service.resolve_export_scope(db, req)
    except ScopeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except service.ExportTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc))

    filename = service.export_filename(site, view, on)
    return StreamingResponse(
        service.iter_export_csv(site, view, on, req),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@analysis_router.get("/scope", response_model=ScopeInfo)
def get_scope(
    view: _View = Query("DAY"),
    site: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _user=Depends(simple_auth.require_permission(ANALYSIS_VIEW)),
) -> ScopeInfo:
    """作用域元信息：可选日期集 + 最新日期。非法站点 → 422。"""
    try:
        return service.get_scope(db, site, view)
    except ScopeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@analysis_router.get("/categories", response_model=list[CategoryOption])
def get_categories(
    view: _View = Query("DAY"),
    site: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _user=Depends(simple_auth.require_permission(ANALYSIS_VIEW)),
) -> list[CategoryOption]:
    """类目下拉选项（按作用域）。"""
    try:
        return service.get_categories(db, site, view)
    except ScopeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
