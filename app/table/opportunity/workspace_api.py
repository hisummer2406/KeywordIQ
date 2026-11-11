"""机会中心工作空间 API（change add-opportunity-ip-agent 决策 11）。瘦控制器：调 service。

挂在 ``/opportunity/workspaces``，每个端点挂 ``opportunity:view``；私有性由 service 带 ``owner_id`` 保证。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import OPPORTUNITY_VIEW
from app.auth.simple_auth import simple_auth
from app.table.opportunity import workspace_service
from app.table.opportunity.ip_schemas import WorkspaceCreate, WorkspaceRename
from database import get_async_db

opportunity_workspace_router = APIRouter()

_require = simple_auth.require_permission(OPPORTUNITY_VIEW)


@opportunity_workspace_router.get("")
async def list_workspaces(db: AsyncSession = Depends(get_async_db), user=Depends(_require)) -> dict[str, Any]:
    return await workspace_service.list_workspaces(db, user.id)


@opportunity_workspace_router.post("")
async def create_workspace(body: WorkspaceCreate, db: AsyncSession = Depends(get_async_db),
                           user=Depends(_require)) -> dict[str, Any]:
    return await workspace_service.create_workspace(db, user.id, body.name)


@opportunity_workspace_router.patch("/{workspace_id}")
async def rename_workspace(workspace_id: int, body: WorkspaceRename, db: AsyncSession = Depends(get_async_db),
                           user=Depends(_require)) -> dict[str, Any]:
    return await workspace_service.rename_workspace(db, user.id, workspace_id, body.name)


@opportunity_workspace_router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(workspace_id: int, db: AsyncSession = Depends(get_async_db),
                           user=Depends(_require)) -> Response:
    await workspace_service.delete_workspace(db, user.id, workspace_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
