# app/auth/simple_auth.py
"""简化版认证模块 - 后台登录验证、当前用户与 RBAC 权限判定"""
import logging
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from database import get_db, SessionFactory
from app.user.user_crud import UserCenterCRUD
from app.user.user_model import UserCenter
from app.auth.login_auth import auth_service

logger = logging.getLogger(__name__)
security = HTTPBearer()


class SimpleAuth:
    """简化认证类"""

    @staticmethod
    async def login(username: str, password: str) -> dict:
        """用户登录"""
        with SessionFactory() as db:
            crud = UserCenterCRUD(db)
            user = crud.authenticate_user(username, password)

            if not user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="用户名或密码错误"
                )

            token = auth_service.create_access_token(
                user.id, user.user_name, user.is_super, user.role_code
            )

            return {
                'access_token': token,
                'token_type': 'bearer',
                'expires_in': 24 * 3600,
                'user': serialize_user(user),
            }

    @staticmethod
    def get_current_user(
            credentials: HTTPAuthorizationCredentials = Depends(security),
            db: Session = Depends(get_db)
    ) -> UserCenter:
        """获取当前用户（含角色/权限，relationship selectin 一并载入）"""
        payload = auth_service.decode_access_token(credentials.credentials)
        if not payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无效令牌"
            )

        crud = UserCenterCRUD(db)
        user = crud.get_user_by_username(payload["username"])
        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户不存在或已禁用"
            )

        return user

    @staticmethod
    def get_super_user():
        """获取超级用户依赖（保留兼容；等价于 admin 角色或 is_super）"""

        def _get_super_user(current_user: UserCenter = Depends(simple_auth.get_current_user)) -> UserCenter:
            if not (current_user.is_super or current_user.role_code == "admin"):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="需要超级管理员权限"
                )
            return current_user

        return _get_super_user

    @staticmethod
    def require_permission(permission: str):
        """RBAC 权限依赖工厂：要求当前用户的角色拥有指定权限。

        用法：``Depends(simple_auth.require_permission("upload:import"))``。
        is_super 视为拥有全部权限（迁移期兜底，避免超管被角色配置意外锁死）。
        """

        def _require(current_user: UserCenter = Depends(simple_auth.get_current_user)) -> UserCenter:
            if current_user.is_super or permission in current_user.permission_codes:
                return current_user
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"缺少权限：{permission}",
            )

        return _require


def serialize_user(user: UserCenter) -> dict:
    """当前用户的干净 REST 形状（供 SPA 的 /token、/me 使用）"""
    return {
        "id": user.id,
        "username": user.user_name,
        "role": user.role_code,
        "permissions": sorted(user.permission_codes),
        "is_active": user.is_active,
        "is_super": user.is_super,
    }


# 全局认证实例
simple_auth = SimpleAuth()
