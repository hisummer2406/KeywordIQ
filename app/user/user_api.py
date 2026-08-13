import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.simple_auth import simple_auth
from app.common.responses import ProTableResponse, protable
from app.user.rbac_model import Role
from app.user.user_crud import UserCenterCRUD
from app.user.user_schemas import RoleOut, UserCenterCreate, UserCenterList, UserCenterUpdate
from database import get_async_db

logger = logging.getLogger(__name__)

user_router = APIRouter()


@user_router.get("/roles", response_model=list[RoleOut])
async def list_roles(
        db: AsyncSession = Depends(get_async_db),
        current_user=Depends(simple_auth.require_super()),
) -> list[RoleOut]:
    """[SPA] 角色下拉数据，供用户管理屏分配角色。"""
    roles = (await db.execute(select(Role).order_by(Role.id))).scalars().all()
    return [RoleOut.model_validate(r) for r in roles]


@user_router.get("/table", response_model=ProTableResponse[UserCenterList])
async def get_users_table(
        page: int = Query(1, ge=1, description="页码"),
        per_page: int = Query(20, ge=1, le=100, description="每页数量"),
        user_name: str | None = Query(None, description="搜索关键词"),
        is_active: bool | None = Query(None, description="用户状态筛选"),
        db: AsyncSession = Depends(get_async_db),
        current_user=Depends(simple_auth.require_super()),
) -> ProTableResponse[UserCenterList]:
    """[SPA] 用户列表。**成功**时返回 ProTable 契约 {data, total, success}。

    失败不再以 HTTP 200 + `success:false` 表达——那让"查询失败"和"没有用户"在前端
    长得一模一样。异常交给全局处理器，ProTable 进入自身的错误态。
    """
    crud = UserCenterCRUD(db)
    result = await crud.get_users_paginated(
        page=page, per_page=per_page, user_name=user_name, is_active=is_active
    )
    return protable(result["items"], result["total"])


@user_router.post("/create", response_model=dict[str, Any])
async def register(
        user_data: UserCenterCreate,
        db: AsyncSession = Depends(get_async_db),
        current_user: dict = Depends(simple_auth.require_super()),
) -> dict[str, Any]:
    """创建用户。用户名重复 → 409。

    此前重复时返 HTTP 200 + `{"status":1}`，前端 `unwrap` 抛出的 Error 无人接管
    （umi 的 errorConfig 只处理 HTTP 层错误），页面既不关弹窗也不提示。

    重名判定在 CRUD 内部完成，这里不再预检——两处判定容易漂移。CRUD 里的预检也只是
    快路径：它与插入之间存在竞态，真正兜底的是 `user_center.user_name` 上的唯一约束，
    冲突会被翻成同一个 409（见 `user_crud._reraise_duplicate_name`）。
    """
    user = await UserCenterCRUD(db).create_user(user_data)

    return {
        "id": user.id,
        "user_name": user.user_name,
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat(),
    }


@user_router.put("/update/{user_id}", response_model=dict[str, Any])
async def update_user(
        user_id: int,
        user_data: UserCenterUpdate,
        db: AsyncSession = Depends(get_async_db),
        current_user: dict = Depends(simple_auth.require_super()),
) -> dict[str, Any]:
    """更新用户信息。用户不存在 → 404，用户名与他人冲突 → 409。"""
    user = await UserCenterCRUD(db).update_user(user_id, user_data)

    return {
        "id": user.id,
        "user_name": user.user_name,
        "is_active": user.is_active,
        "is_super": user.is_super,
        "updated_at": user.updated_at.isoformat(),
    }


@user_router.post("/toggle-status/{user_id}", response_model=dict[str, Any])
async def toggle_user_status(
        user_id: int,
        db: AsyncSession = Depends(get_async_db),
        current_user: dict = Depends(simple_auth.require_super()),
) -> dict[str, Any]:
    """切换用户状态（激活/禁用）。用户不存在 → 404，禁用超管 → 409。"""
    user = await UserCenterCRUD(db).toggle_user_status(user_id)
    return {"id": user.id, "is_active": user.is_active}
