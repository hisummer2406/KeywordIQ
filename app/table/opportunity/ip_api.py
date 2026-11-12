"""机会中心 IP 专家 API（change add-opportunity-ip-agent）。瘦控制器：调 service。

挂在 ``/opportunity/ip``。每个端点都挂 ``opportunity:view``（默认仅超管，opportunity-center
规格）；私有性由 service 每条查询带 ``owner_id`` 保证。

- ``GET  /meta``
- ``POST /researches``、``GET /researches``、``GET /researches/{id}``
- 显式动作（202，只写任务行）：``POST /researches/{id}/plan``、``/searches``、``/details``、``/analysis``
- ``GET  /researches/{id}/candidates/{cid}/image``：摘要附图
- ``POST /researches/{id}/reviews``：追加人工意见
- ``PATCH /researches/{id}/workspace``：移入/移出工作空间（决策 11）
- ``POST /researches/{id}/messages``：对话追问（202，决策 12）
- ``GET /schedules``、``PUT|DELETE /researches/{id}/schedule``：定时复检（决策 13）
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import OPPORTUNITY_VIEW, RISK_SEARCH
from app.auth.simple_auth import simple_auth
from app.table.opportunity import research_service, schedule_service
from app.table.opportunity.ip_schemas import (
    DetailRequest,
    MessageCreate,
    MoveRequest,
    ResearchCreate,
    ReviewCreate,
    ScheduleUpsert,
    SearchRequest,
    TokenRequest,
)
from database import get_async_db

opportunity_ip_router = APIRouter()

_require = simple_auth.require_permission(OPPORTUNITY_VIEW)
# 会向智慧芽计费的动作（执行检索、取证、设定时复检）另外要求 risk:search：该权限点按既有约定
# 只默认给 admin，「检索按次计费，不能随角色默认铺开」（permissions.py）。机会中心授予运营后
# （change add-opportunity-dossier），不加这道门运营就能绕过它发起计费检索。
_require_paid = simple_auth.require_permission(RISK_SEARCH)


def _accepted(body: dict[str, Any]) -> JSONResponse:
    return JSONResponse(status_code=202, content=body)


@opportunity_ip_router.get("/meta")
async def meta(db: AsyncSession = Depends(get_async_db), user=Depends(_require)) -> dict[str, Any]:
    return await research_service.meta(db)


@opportunity_ip_router.post("/researches")
async def create_research(body: ResearchCreate, db: AsyncSession = Depends(get_async_db),
                          user=Depends(_require)) -> dict[str, Any]:
    return await research_service.create_research(db, user.id, body)


@opportunity_ip_router.get("/researches")
async def list_researches(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                          workspace_id: int | None = Query(None, ge=1),
                          unassigned: bool = Query(False, description="只看未归类的任务"),
                          db: AsyncSession = Depends(get_async_db), user=Depends(_require)) -> dict[str, Any]:
    return await research_service.list_researches(db, user.id, page=page, page_size=page_size,
                                                  workspace_id=workspace_id, unassigned=unassigned)


@opportunity_ip_router.get("/researches/{research_id}")
async def get_research(research_id: int, db: AsyncSession = Depends(get_async_db),
                       user=Depends(_require)) -> dict[str, Any]:
    return await research_service.get_research(db, user.id, research_id)


@opportunity_ip_router.post("/researches/{research_id}/plan", status_code=202)
async def plan(research_id: int, body: TokenRequest, db: AsyncSession = Depends(get_async_db),
               user=Depends(_require)) -> JSONResponse:
    return _accepted(await research_service.request_plan(db, user.id, research_id, body.client_token))


@opportunity_ip_router.post("/researches/{research_id}/searches", status_code=202)
async def search(research_id: int, body: SearchRequest, db: AsyncSession = Depends(get_async_db),
                 user=Depends(_require),
                 _paid=Depends(_require_paid)) -> JSONResponse:
    return _accepted(await research_service.request_search(db, user.id, research_id, body))


@opportunity_ip_router.post("/researches/{research_id}/details", status_code=202)
async def details(research_id: int, body: DetailRequest, db: AsyncSession = Depends(get_async_db),
                  user=Depends(_require),
                  _paid=Depends(_require_paid)) -> JSONResponse:
    return _accepted(await research_service.request_details(db, user.id, research_id, body))


@opportunity_ip_router.post("/researches/{research_id}/analysis", status_code=202)
async def analysis(research_id: int, body: TokenRequest, db: AsyncSession = Depends(get_async_db),
                   user=Depends(_require)) -> JSONResponse:
    return _accepted(await research_service.request_analysis(db, user.id, research_id, body.client_token))


@opportunity_ip_router.get("/researches/{research_id}/candidates/{candidate_id}/image")
async def candidate_image(research_id: int, candidate_id: int, db: AsyncSession = Depends(get_async_db),
                          user=Depends(_require)) -> Response:
    data, ctype = await research_service.get_candidate_image(db, user.id, research_id, candidate_id)
    return Response(content=data, media_type=ctype,
                    headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


@opportunity_ip_router.post("/researches/{research_id}/reviews")
async def add_review(research_id: int, body: ReviewCreate, db: AsyncSession = Depends(get_async_db),
                     user=Depends(_require)) -> dict[str, Any]:
    return await research_service.add_review(db, user.id, research_id, body)


@opportunity_ip_router.patch("/researches/{research_id}/workspace")
async def move_research(research_id: int, body: MoveRequest, db: AsyncSession = Depends(get_async_db),
                        user=Depends(_require)) -> dict[str, Any]:
    return await research_service.move_research(db, user.id, research_id, body.workspace_id)


@opportunity_ip_router.post("/researches/{research_id}/messages", status_code=202)
async def ask(research_id: int, body: MessageCreate, db: AsyncSession = Depends(get_async_db),
              user=Depends(_require)) -> JSONResponse:
    return _accepted(await research_service.request_ask(db, user.id, research_id, body))


@opportunity_ip_router.get("/schedules")
async def list_schedules(db: AsyncSession = Depends(get_async_db), user=Depends(_require)) -> dict[str, Any]:
    return await schedule_service.list_schedules(db, user.id)


@opportunity_ip_router.put("/researches/{research_id}/schedule")
async def upsert_schedule(research_id: int, body: ScheduleUpsert, db: AsyncSession = Depends(get_async_db),
                          user=Depends(_require),
                          _paid=Depends(_require_paid)) -> dict[str, Any]:
    return await schedule_service.upsert_schedule(db, user.id, research_id, body)


@opportunity_ip_router.delete("/researches/{research_id}/schedule", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(research_id: int, db: AsyncSession = Depends(get_async_db),
                          user=Depends(_require)) -> Response:
    await schedule_service.delete_schedule(db, user.id, research_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
