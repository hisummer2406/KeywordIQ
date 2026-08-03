import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.permissions import USER_MANAGE
from app.auth.simple_auth import simple_auth
from app.common.errors import CODE_USER_DUPLICATE_NAME, ConflictError
from app.common.responses import ProTableResponse, protable
from app.user.rbac_model import Role
from app.user.user_crud import UserCenterCRUD
from app.user.user_errors import DuplicateUserNameError, UserNotFoundError
from app.user.user_schemas import RoleOut, UserCenterCreate, UserCenterList, UserCenterUpdate
from database import get_db

logger = logging.getLogger(__name__)

user_router = APIRouter()


def _crud_failure(message: str):
    """把 CRUD 的 ``(success, message, user)`` 元组式失败转成语义正确的异常。

    CRUD 层保留元组签名（改造它不在本变更范围内），但**失败不允许越过 API 边界继续以
    返回值形式存在**——那正是漏检即静默的来源。这里按 message 归类到具体状态码，
    未识别的归 409（与当前状态冲突），而不是笼统的 500。
    """
    if "不存在" in message:
        return UserNotFoundError(message)
    return ConflictError(message, code=CODE_USER_DUPLICATE_NAME if "用户名" in message else None)


@user_router.get("/roles", response_model=list[RoleOut])
async def list_roles(
        db: Session = Depends(get_db),
        current_user=Depends(simple_auth.require_permission(USER_MANAGE)),
) -> list[RoleOut]:
    """[SPA] 角色下拉数据，供用户管理屏分配角色。"""
    roles = db.execute(select(Role).order_by(Role.id)).scalars().all()
    return [RoleOut.model_validate(r) for r in roles]


@user_router.get("/table", response_model=ProTableResponse[UserCenterList])
async def get_users_table(
        page: int = Query(1, ge=1, description="页码"),
        per_page: int = Query(20, ge=1, le=100, description="每页数量"),
        user_name: str | None = Query(None, description="搜索关键词"),
        is_active: bool | None = Query(None, description="用户状态筛选"),
        db: Session = Depends(get_db),
        current_user=Depends(simple_auth.require_permission(USER_MANAGE)),
) -> ProTableResponse[UserCenterList]:
    """[SPA] 用户列表。**成功**时返回 ProTable 契约 {data, total, success}。

    失败不再以 HTTP 200 + `success:false` 表达——那让"查询失败"和"没有用户"在前端
    长得一模一样。异常交给全局处理器，ProTable 进入自身的错误态。
    """
    crud = UserCenterCRUD(db)
    result = crud.get_users_paginated(
        page=page, per_page=per_page, user_name=user_name, is_active=is_active
    )
    return protable(result["items"], result["total"])


@user_router.post("/create", response_model=dict[str, Any])
async def register(
        user_data: UserCenterCreate,
        db: Session = Depends(get_db),
        current_user: dict = Depends(simple_auth.require_permission(USER_MANAGE)),
) -> dict[str, Any]:
    """创建用户。用户名重复 → 409。

    此前重复时返 HTTP 200 + `{"status":1}`，前端 `unwrap` 抛出的 Error 无人接管
    （umi 的 errorConfig 只处理 HTTP 层错误），页面既不关弹窗也不提示。
    """
    crud = UserCenterCRUD(db)
    if crud.get_user_by_username(user_data.user_name):
        raise DuplicateUserNameError()

    success, message, user = crud.create_user(user_data)
    if not success:
        raise _crud_failure(message)

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
        db: Session = Depends(get_db),
        current_user: dict = Depends(simple_auth.require_permission(USER_MANAGE)),
) -> dict[str, Any]:
    """更新用户信息。用户不存在 → 404，用户名与他人冲突 → 409。"""
    crud = UserCenterCRUD(db)
    success, message, user = crud.update_user(user_id, user_data)
    if not success:
        raise _crud_failure(message)

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
        db: Session = Depends(get_db),
        current_user: dict = Depends(simple_auth.require_permission(USER_MANAGE)),
) -> dict[str, Any]:
    """切换用户状态（激活/禁用）。用户不存在 → 404，禁用超管 → 409。"""
    crud = UserCenterCRUD(db)
    success, message, user = crud.toggle_user_status(user_id)
    if not success:
        raise _crud_failure(message)

    return {"id": user.id, "is_active": user.is_active}
