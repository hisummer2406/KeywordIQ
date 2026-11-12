"""机会中心线索 API（change add-opportunity-signal-leads）。瘦控制器。

挂 ``opportunity:view``，不要求 ``keyword:view``：增长榜是全员共享的 ABA 派生数据，不含任何用户
私有内容；额外返回的档案归属本来就是机会中心的数据。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import OPPORTUNITY_VIEW
from app.auth.simple_auth import simple_auth
from app.table.opportunity import leads_service
from database import get_async_db

opportunity_leads_router = APIRouter()

_require = simple_auth.require_permission(OPPORTUNITY_VIEW)


@opportunity_leads_router.get("/growth")
async def growth_leads(
    # 站点必填：缺失即 422，不回退默认站点（multi-site-data-model；AGENTS「非法/缺失站点码必须报错」）
    site: str = Query(..., min_length=1, max_length=8),
    on: date | None = Query(None, alias="date", description="锚定日，缺省取该站点最新可用日期"),
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_async_db), user=Depends(_require),
) -> dict[str, Any]:
    return await leads_service.growth_leads(db, site=site, on=on, page=page, page_size=page_size)
