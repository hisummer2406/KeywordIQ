import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from sqlalchemy import select

from database import get_db
from app.user.user_crud import UserCenterCRUD
from app.user.user_model import UserCenter
from app.user.rbac_model import Role
from app.user.user_schemas import (UserCenterCreate, UserCenterUpdate, UserCenterList, RoleOut)
from app.auth.simple_auth import simple_auth
from app.auth.permissions import USER_MANAGE
from app.common.responses import ProTableResponse, protable, protable_error

logger = logging.getLogger(__name__)

user_router = APIRouter()


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
        user_name: Optional[str] = Query(None, description="搜索关键词"),
        is_active: Optional[bool] = Query(None, description="用户状态筛选"),
        db: Session = Depends(get_db),
        current_user=Depends(simple_auth.require_permission(USER_MANAGE)),
) -> ProTableResponse[UserCenterList]:
    """[SPA] 用户列表，返回 ProTable 契约 {data, total, success}。"""
    try:
        crud = UserCenterCRUD(db)
        result = crud.get_users_paginated(
            page=page, per_page=per_page, user_name=user_name, is_active=is_active
        )
        return protable(result["items"], result["total"])
    except Exception as e:
        logger.error(f"获取用户表格失败: {e}")
        return protable_error("获取用户列表失败")


@user_router.post("/create", response_model=Dict[str, Any])
async def register(
        user_data: UserCenterCreate,
        db: Session = Depends(get_db),
        current_user: dict = Depends(simple_auth.require_permission(USER_MANAGE)),
) -> Dict[str, Any]:
    """用户注册"""
    try:
        crud = UserCenterCRUD(db)
        exist = crud.get_user_by_username(user_data.user_name)
        if exist:
            return {
                "status": 1,
                "msg": "用户名已存在"
            }

        success, message, user = crud.create_user(user_data)

        return {
            "status": 0,
            "msg": '创建成功',
            "data": {
                "id": user.id,
                "user_name": user.user_name,
                "is_active": user.is_active,
                "created_at": user.created_at.isoformat()
            }
        }
    except Exception as e:
        logger.error(f"创建用户失败: {e}")
        raise HTTPException(status_code=500, detail="创建用户失败")


@user_router.put("/update/{user_id}", response_model=Dict[str, Any])
async def update_user(
        user_id: int,
        user_data: UserCenterUpdate,
        db: Session = Depends(get_db),
        current_user: dict = Depends(simple_auth.require_permission(USER_MANAGE)),
) -> Dict[str, Any]:
    """更新用户信息"""
    try:
        crud = UserCenterCRUD(db)
        success, message, user = crud.update_user(user_id, user_data)

        if not success:
            return {
                "status": 1,
                "msg": message
            }

        return {
            "status": 0,
            "msg": message,
            "data": {
                "id": user.id,
                "user_name": user.user_name,
                "is_active": user.is_active,
                "is_super": user.is_super,
                "updated_at": user.updated_at.isoformat()
            }
        }

    except Exception as e:
        logger.error(f"更新用户失败: {e}")
        return {
            "status": 1,
            "msg": "更新用户失败"
        }


@user_router.post("/toggle-status/{user_id}", response_model=Dict[str, Any])
async def toggle_user_status(
        user_id: int,
        db: Session = Depends(get_db),
        current_user: dict = Depends(simple_auth.require_permission(USER_MANAGE)),
) -> Dict[str, Any]:
    """切换用户状态（激活/禁用）"""
    try:
        crud = UserCenterCRUD(db)
        success, message, user = crud.toggle_user_status(user_id)

        if not success:
            return {
                "status": 1,
                "msg": message
            }

        return {
            "status": 0,
            "msg": message,
            "data": {
                "id": user.id,
                "is_active": user.is_active
            }
        }

    except Exception as e:
        logger.error(f"切换用户状态失败: {e}")
        raise HTTPException(status_code=500, detail="状态切换失败")
