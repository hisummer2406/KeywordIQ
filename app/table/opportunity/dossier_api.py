"""机会档案 API（change add-opportunity-dossier）。瘦控制器：只做参数与权限，业务在 service。

挂在 ``/opportunity/dossiers``，每个端点各自挂 ``opportunity:view``（opportunity-center 规格：
菜单隐藏不是访问控制）。档案团队可见，service 不按创建人过滤；关联 IP 调研只接受当前用户名下的。

只有追问会产生外部调用（大模型），它只写队列、返回 202，由后台执行。
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import OPPORTUNITY_VIEW, PRICING_VIEW
from app.auth.simple_auth import simple_auth
from app.table.opportunity import dossier_ask, dossier_service
from app.table.opportunity.dossier_model import DOSSIER_STATUSES
from app.table.opportunity.dossier_schemas import (
    AskCreate,
    DecisionCreate,
    DossierCreate,
    DossierUpdate,
    KeywordsAdd,
    ManualEvidenceCreate,
)
from database import get_async_db

opportunity_dossier_router = APIRouter()

_require = simple_auth.require_permission(OPPORTUNITY_VIEW)
# 从档案建核价单另要求核价的既有权限点；档案里的测算摘要只要求 opportunity:view（摘要是档案的一部分）
_require_pricing = simple_auth.require_permission(PRICING_VIEW)

DossierStatus = Literal[DOSSIER_STATUSES]  # type: ignore[valid-type]


@opportunity_dossier_router.get("")
async def list_dossiers(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    status_: DossierStatus | None = Query(None, alias="status"),
    due: bool = Query(False, description="只看待复查（复查日期不晚于今天）"),
    mine: bool = Query(False, description="只看我负责的"),
    archived: bool = Query(False, description="看已归档的"),
    q: str | None = Query(None, max_length=100, description="按标题或关键词搜索"),
    site: str | None = Query(None, min_length=1, max_length=8, description="只看某个站点的档案；非法站点 422"),
    db: AsyncSession = Depends(get_async_db), user=Depends(_require),
) -> dict[str, Any]:
    return await dossier_service.list_dossiers(db, user.id, page=page, page_size=page_size, status=status_,
                                               due=due, mine=mine, archived=archived, q=q, site=site)


@opportunity_dossier_router.post("", status_code=status.HTTP_201_CREATED)
async def create_dossier(body: DossierCreate, db: AsyncSession = Depends(get_async_db),
                         user=Depends(_require)) -> dict[str, Any]:
    return await dossier_service.create_dossier(db, user.id, body)


@opportunity_dossier_router.get("/linkable-researches")
async def linkable_researches(db: AsyncSession = Depends(get_async_db), user=Depends(_require)) -> dict[str, Any]:
    return await dossier_service.my_linkable_researches(db, user.id)


@opportunity_dossier_router.get("/linkable-pricing-sheets")
async def linkable_pricing_sheets(db: AsyncSession = Depends(get_async_db),
                                  user=Depends(_require)) -> dict[str, Any]:
    return await dossier_service.my_linkable_pricing_sheets(db, user.id)


@opportunity_dossier_router.get("/{dossier_id}")
async def get_dossier(dossier_id: int, db: AsyncSession = Depends(get_async_db),
                      user=Depends(_require)) -> dict[str, Any]:
    return await dossier_service.get_dossier(db, user.id, dossier_id)


@opportunity_dossier_router.patch("/{dossier_id}")
async def update_dossier(dossier_id: int, body: DossierUpdate, db: AsyncSession = Depends(get_async_db),
                         user=Depends(_require)) -> dict[str, Any]:
    return await dossier_service.update_dossier(db, user.id, dossier_id, body)


@opportunity_dossier_router.post("/{dossier_id}/keywords")
async def add_keywords(dossier_id: int, body: KeywordsAdd, db: AsyncSession = Depends(get_async_db),
                       user=Depends(_require)) -> dict[str, Any]:
    return await dossier_service.add_keywords(db, user.id, dossier_id, body.keywords)


@opportunity_dossier_router.delete("/{dossier_id}/keywords/{keyword_row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_keyword(dossier_id: int, keyword_row_id: int, db: AsyncSession = Depends(get_async_db),
                         user=Depends(_require)) -> Response:
    await dossier_service.remove_keyword(db, user.id, dossier_id, keyword_row_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@opportunity_dossier_router.post("/{dossier_id}/snapshots")
async def refresh_snapshot(dossier_id: int, db: AsyncSession = Depends(get_async_db),
                           user=Depends(_require)) -> dict[str, Any]:
    return await dossier_service.refresh_snapshot(db, user.id, dossier_id)


@opportunity_dossier_router.post("/{dossier_id}/evidence", status_code=status.HTTP_201_CREATED)
async def add_manual_evidence(dossier_id: int, body: ManualEvidenceCreate, db: AsyncSession = Depends(get_async_db),
                              user=Depends(_require)) -> dict[str, Any]:
    return await dossier_service.add_manual_evidence(db, user.id, dossier_id, body)


@opportunity_dossier_router.post("/{dossier_id}/evidence/{evidence_id}/void")
async def void_manual_evidence(dossier_id: int, evidence_id: int, db: AsyncSession = Depends(get_async_db),
                               user=Depends(_require)) -> dict[str, Any]:
    return await dossier_service.void_manual_evidence(db, user.id, dossier_id, evidence_id)


@opportunity_dossier_router.post("/{dossier_id}/decisions", status_code=status.HTTP_201_CREATED)
async def add_decision(dossier_id: int, body: DecisionCreate, db: AsyncSession = Depends(get_async_db),
                       user=Depends(_require)) -> dict[str, Any]:
    return await dossier_service.add_decision(db, user.id, dossier_id, body)


@opportunity_dossier_router.put("/{dossier_id}/ip-researches/{research_id}")
async def link_research(dossier_id: int, research_id: int, db: AsyncSession = Depends(get_async_db),
                        user=Depends(_require)) -> dict[str, Any]:
    return await dossier_service.link_research(db, user.id, dossier_id, research_id)


@opportunity_dossier_router.delete("/{dossier_id}/ip-researches/{research_id}",
                                   status_code=status.HTTP_204_NO_CONTENT)
async def unlink_research(dossier_id: int, research_id: int, db: AsyncSession = Depends(get_async_db),
                          user=Depends(_require)) -> Response:
    await dossier_service.unlink_research(db, user.id, dossier_id, research_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@opportunity_dossier_router.post("/{dossier_id}/messages", status_code=202)
async def ask(dossier_id: int, body: AskCreate, db: AsyncSession = Depends(get_async_db),
              user=Depends(_require)) -> JSONResponse:
    return JSONResponse(status_code=202, content=await dossier_ask.request_ask(db, user.id, dossier_id, body))


@opportunity_dossier_router.post("/{dossier_id}/pricing-sheets", status_code=status.HTTP_201_CREATED)
async def create_pricing_sheet(dossier_id: int, db: AsyncSession = Depends(get_async_db),
                               user=Depends(_require), _pricing=Depends(_require_pricing)) -> dict[str, Any]:
    return await dossier_service.create_pricing_sheet(db, user.id, dossier_id)


@opportunity_dossier_router.put("/{dossier_id}/pricing-sheets/{sheet_id}")
async def link_pricing_sheet(dossier_id: int, sheet_id: int, db: AsyncSession = Depends(get_async_db),
                             user=Depends(_require)) -> dict[str, Any]:
    return await dossier_service.link_pricing_sheet(db, user.id, dossier_id, sheet_id)


@opportunity_dossier_router.delete("/{dossier_id}/pricing-sheets/{sheet_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_pricing_sheet(dossier_id: int, sheet_id: int, db: AsyncSession = Depends(get_async_db),
                               user=Depends(_require)) -> Response:
    await dossier_service.unlink_pricing_sheet(db, user.id, dossier_id, sheet_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
