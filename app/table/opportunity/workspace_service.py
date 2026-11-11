"""机会中心工作空间（change add-opportunity-ip-agent 决策 11）。

按创建人私有；任务（专利调研）用 ``opp_ip_research.workspace_id`` 归入。有任务的工作空间不能删，
避免误删后任务全部散到「未归类」。同名靠唯一约束兜底，重复点击「新建」得到 409 而不是两份。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.table.opportunity.ip_errors import WorkspaceNameTakenError, WorkspaceNotEmptyError
from app.table.opportunity.ip_model import OppIpResearch, OppWorkspace
from app.table.opportunity.research_service import owned_workspace

#: 每个用户的工作空间数量上限（列表不分页）
MAX_WORKSPACES = 100


def _out(ws: OppWorkspace, count: int) -> dict[str, Any]:
    return {"id": ws.id, "name": ws.name, "task_count": count, "created_at": ws.created_at}


async def list_workspaces(db: AsyncSession, owner_id: int) -> dict[str, Any]:
    counts = (select(func.count()).where(OppIpResearch.workspace_id == OppWorkspace.id)
              .correlate(OppWorkspace).scalar_subquery())
    rows = (await db.execute(
        select(OppWorkspace, counts).where(OppWorkspace.owner_id == owner_id)
        .order_by(OppWorkspace.created_at, OppWorkspace.id).limit(MAX_WORKSPACES))).all()
    unassigned = (await db.execute(select(func.count()).select_from(OppIpResearch).where(
        OppIpResearch.owner_id == owner_id, OppIpResearch.workspace_id.is_(None)))).scalar_one()
    total = (await db.execute(select(func.count()).select_from(OppIpResearch).where(
        OppIpResearch.owner_id == owner_id))).scalar_one()
    return {"items": [_out(ws, int(n or 0)) for ws, n in rows], "unassigned_count": unassigned, "total_count": total}


async def create_workspace(db: AsyncSession, owner_id: int, name: str) -> dict[str, Any]:
    ws = OppWorkspace(owner_id=owner_id, name=name)
    db.add(ws)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise WorkspaceNameTakenError(log_detail=f"owner={owner_id}") from None
    return _out(ws, 0)


async def rename_workspace(db: AsyncSession, owner_id: int, workspace_id: int, name: str) -> dict[str, Any]:
    ws = await owned_workspace(db, owner_id, workspace_id)
    ws.name = name
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise WorkspaceNameTakenError(log_detail=f"owner={owner_id}") from None
    count = (await db.execute(select(func.count()).select_from(OppIpResearch).where(
        OppIpResearch.workspace_id == workspace_id))).scalar_one()
    return _out(ws, count)


async def delete_workspace(db: AsyncSession, owner_id: int, workspace_id: int) -> None:
    ws = await owned_workspace(db, owner_id, workspace_id)
    # 先持行锁再计数：并发「移入」写外键时要取该行的 KEY SHARE 锁，与这里的 FOR UPDATE 互斥，
    # 所以计数要么看到已提交的移入（拒绝删除），要么移入在删除后因外键失败，不会留下悬空引用
    await db.execute(select(OppWorkspace.id).where(OppWorkspace.id == ws.id).with_for_update())
    count = (await db.execute(select(func.count()).select_from(OppIpResearch).where(
        OppIpResearch.workspace_id == ws.id))).scalar_one()
    if count:
        raise WorkspaceNotEmptyError(log_detail=f"workspace={ws.id} tasks={count}")
    await db.delete(ws)
    await db.commit()
