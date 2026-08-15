"""工具箱 · 持续增长市场 API。瘦控制器：调 service，异常交给全局处理器。

- POST /api/toolbox/growth/search  分页查询，ProTable 契约 + 作用域回显 + 缺期
- GET  /api/toolbox/growth/scope   可选日期集 + 判定口径（界面口径说明条据此拼文案）

两个端点都挂 `toolbox:view`。**没有全局鉴权中间件**——新增端点漏挂依赖就是裸奔的，
这不是可以留给下一次补的事。

失败一律**抛**不**返**：`GrowthScopeError`（422）是 `APIError` 子类，由
`app/common/error_handlers.py` 统一产出错误体；非法排序字段由 schema 的 Literal 挡在
参数校验层（400）。路由层不做 try/except 转译——那会把域内错误码盖成通用码。
"""
import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import TOOLBOX_VIEW
from app.auth.simple_auth import simple_auth
from app.table.toolbox import growth_service as service
from app.table.toolbox.growth_schemas import (
    GrowthScopeInfo,
    GrowthSearchRequest,
    GrowthSearchResponse,
)
from database import get_async_db

logger = logging.getLogger(__name__)

toolbox_router = APIRouter()


@toolbox_router.post("/growth/search", response_model=GrowthSearchResponse)
async def search_growth(
    req: GrowthSearchRequest,
    db: AsyncSession = Depends(get_async_db),
    _user=Depends(simple_auth.require_permission(TOOLBOX_VIEW)),
) -> GrowthSearchResponse:
    """作用域（站点 + 锚定日）内的持续增长榜，分页。"""
    return await service.search(db, req)


@toolbox_router.get("/growth/scope", response_model=GrowthScopeInfo)
async def get_growth_scope(
    site: str | None = Query(None),
    db: AsyncSession = Depends(get_async_db),
    _user=Depends(simple_auth.require_permission(TOOLBOX_VIEW)),
) -> GrowthScopeInfo:
    """可选日期集 + 判定口径。非法站点 → 422。"""
    return await service.get_scope(db, site)
