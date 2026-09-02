"""站点作用域查询 API（V2）。瘦控制器：调 service，异常交给全局处理器。

- POST /api/analysis/search   分页查询，成功返回 ProTable 契约 + 作用域回显
- POST /api/analysis/export   作用域内流式导出 CSV
- GET  /api/analysis/scope    作用域元信息（可选日期集 + 最新日期），日期选择器用
- GET  /api/analysis/categories  类目下拉选项（按作用域）

失败一律**抛**不**返**：`ScopeError`（422）与 `ExportTooLargeError`（413）都是 `APIError`
子类，由 `app/common/error_handlers.py` 统一产出错误体。这里不再 try/except 转译——
路由层做转译会把域内错误码（`analysis.scope_invalid`）盖成通用码。

**执行模型**：search / scope / categories 是 `async def` + `AsyncSession`（asyncpg）——
这是唯一随用户数增长的路径。export 保持 `def` + 同步会话：导出走服务端游标，且流式
生成器需自建会话。`def` 端点依赖 `async` 鉴权依赖是允许的——依赖在事件循环上解析，
端点本身仍进线程池。
"""
import logging

# 别名引用日期类型：下方端点有名为 ``date`` 的查询参数，直接用 ``date`` 作注解会被它遮蔽。
from datetime import date as _Date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.auth.permissions import ANALYSIS_EXPORT, ANALYSIS_VIEW
from app.auth.simple_auth import simple_auth
from app.table.analysis import analysis_service as service
from app.table.search.search_schemas import (
    AnalysisSearchRequest,
    AnalysisSearchResponse,
    BrandOption,
    CategoryOption,
    ScopeInfo,
)
from database import get_async_db, get_db

logger = logging.getLogger(__name__)

analysis_router = APIRouter()

_View = Literal["DAY", "WEEK"]


@analysis_router.post("/search", response_model=AnalysisSearchResponse)
async def search_data(
    req: AnalysisSearchRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(simple_auth.require_permission(ANALYSIS_VIEW)),
) -> AnalysisSearchResponse:
    """作用域内分页查询。

    作用域非法时抛 `ScopeError` → 422。此前是返 HTTP 200 + `success:false`，
    前端只能表现为一个没有解释的空表格。
    """
    return await service.search(db, req, user.id)


@analysis_router.post("/export")
def export_data(
    req: AnalysisSearchRequest,
    db: Session = Depends(get_db),
    user=Depends(simple_auth.require_permission(ANALYSIS_EXPORT)),
) -> StreamingResponse:
    """把当前作用域与筛选下的结果流式导出为 CSV。

    与 /search 同一个请求体，故导出的永远是用户当下看到的那一份数据（分页参数忽略）。

    `resolve_export_scope` 的全部校验（作用域、规模上限）都在返回 `StreamingResponse`
    **之前**完成——一旦开流，状态码与响应头已发出，之后再抛异常只会得到一份被截断的
    200 CSV，而它会被当成完整数据使用。
    """
    site, view, on = service.resolve_export_scope(db, req, user.id)

    filename = service.export_filename(site, view, on)
    return StreamingResponse(
        service.iter_export_csv(site, view, on, req, user.id),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@analysis_router.get("/scope", response_model=ScopeInfo)
async def get_scope(
    view: _View = Query("DAY"),
    site: str | None = Query(None),
    db: AsyncSession = Depends(get_async_db),
    _user=Depends(simple_auth.require_permission(ANALYSIS_VIEW)),
) -> ScopeInfo:
    """作用域元信息：可选日期集 + 最新日期。非法站点 → 422。"""
    return await service.get_scope(db, site, view)


@analysis_router.get("/categories", response_model=list[CategoryOption])
async def get_categories(
    view: _View = Query("DAY"),
    site: str | None = Query(None),
    date: _Date | None = Query(None, description="报告日期，缺省取该作用域最新可用日期"),
    db: AsyncSession = Depends(get_async_db),
    _user=Depends(simple_auth.require_permission(ANALYSIS_VIEW)),
) -> list[CategoryOption]:
    """类目下拉选项（按作用域三元组）。

    **日期是选项口径的一部分**：统计按 (站点, 周期, 日期) 分片，与筛选严格同粒度。
    不传则取最新可用日期——这是给"还没选日期"的首屏用的，不是"不限日期"。
    """
    return await service.get_categories(db, site, view, date)


@analysis_router.get("/brands", response_model=list[BrandOption])
async def get_brands(
    view: _View = Query("DAY"),
    site: str | None = Query(None),
    date: _Date | None = Query(None, description="报告日期，缺省取该作用域最新可用日期"),
    db: AsyncSession = Depends(get_async_db),
    _user=Depends(simple_auth.require_permission(ANALYSIS_VIEW)),
) -> list[BrandOption]:
    """品牌下拉选项（按作用域三元组）。与 /categories 同构，同一套权限点。"""
    return await service.get_brands(db, site, view, date)
