"""关键词排名趋势详情 API。市场分析与我的词库共用同一个只读端点。

权限接受 `analysis:view` 或 `keyword:view` 任一——端点本身只返回一个公共 keyword_id
的历史排名，不携带任何域私有数据，两个入口各自的合法用户都不该被另一个域的权限矩阵
挡住。见 openspec 变更 `add-keyword-trend-detail` 的 design.md 决策 6。
"""
from datetime import date as _Date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import ANALYSIS_VIEW, KEYWORD_VIEW
from app.auth.simple_auth import simple_auth
from app.table.keyword import trend_service as service
from app.table.keyword.trend_schemas import KeywordTrendResponse, TrendRange
from database import get_async_db

trend_router = APIRouter()

_require_view = simple_auth.require_any_permission(ANALYSIS_VIEW, KEYWORD_VIEW)


@trend_router.get("/trend", response_model=KeywordTrendResponse)
async def get_keyword_trend(
    keyword_id: int = Query(..., description="dim_keyword.keyword_id"),
    site: str = Query(..., description="站点码，必须与 keyword_id 实际归属的站点一致"),
    view: Literal["DAY", "WEEK"] = Query("DAY"),
    range: TrendRange = Query("RECENT_30D"),  # 参数名对齐前端约定；与内置 range() 无关，函数体不用到它
    on: _Date | None = Query(
        None, description="来源锚定期；缺省取该 (site,view) 最新一期，"
                           "跨视图切换时取 <= 该日期的最新一期"
    ),
    db: AsyncSession = Depends(get_async_db),
    _user=Depends(_require_view),
) -> KeywordTrendResponse:
    """单关键词的日/周排名历史。keyword_id 与 site 不一致时 422。"""
    return await service.get_keyword_trend(
        db, keyword_id=keyword_id, site=site, view=view, range_=range, on=on
    )
