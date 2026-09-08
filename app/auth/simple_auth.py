# app/auth/simple_auth.py
"""简化版认证模块 - 后台登录验证、当前用户与 RBAC 权限判定。

全链路异步（AsyncSession + asyncpg）。bcrypt 是 CPU 密集，由 user_crud 经
run_in_threadpool 移出事件循环——此前 login 在事件循环上直接跑 247 ms 的 bcrypt，
每次登录都让该 worker 停摆同等时长。

role 与 permissions 两个 relationship 都是 lazy="selectin"（预加载），故
AsyncSession 下访问 role_code / permission_codes 不会触发禁止的隐式懒加载。
"""
import logging

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.login_auth import auth_service
from app.user.user_crud import UserCenterCRUD
from app.user.user_model import UserCenter
from database import AsyncSessionFactory, get_async_db

logger = logging.getLogger(__name__)
security = HTTPBearer()


class SimpleAuth:
    """简化认证类"""

    @staticmethod
    async def login(username: str, password: str) -> dict:
        """用户登录"""
        async with AsyncSessionFactory() as db:
            crud = UserCenterCRUD(db)
            user = await crud.authenticate_user(username, password)

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
    async def get_current_user(
            credentials: HTTPAuthorizationCredentials = Depends(security),
            db: AsyncSession = Depends(get_async_db)
    ) -> UserCenter:
        """获取当前用户（含角色/权限，relationship selectin 一并载入）"""
        payload = auth_service.decode_access_token(credentials.credentials)
        if not payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无效令牌"
            )

        crud = UserCenterCRUD(db)
        user = await crud.get_user_by_username(payload["username"])
        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户不存在或已禁用"
            )

        return user

    @staticmethod
    def require_super():
        """要求 ``is_super``。用于**不可授予**的能力——用户管理。

        判据只看 `is_super`，**不看角色码**。取代了此前那个「is_super 或 role_code ==
        'admin'」的 `get_super_user`（声明后从未被使用）：把角色码也算作超管，等于让
        「谁能管用户」变成一件可以通过改角色配置来改变的事，而这正是本守卫要排除的。

        与 :meth:`require_permission` 的分工：权限点是**可授予**的能力，走角色配置；
        这里是不可授予的能力，没有对应权限点（`user:manage` 已从矩阵中移除——一个
        勾了也不生效的权限比没有更糟）。
        """

        def _require_super(current_user: UserCenter = Depends(simple_auth.get_current_user)) -> UserCenter:
            if not current_user.is_super:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="需要超级管理员权限",
                )
            return current_user

        return _require_super

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

    @staticmethod
    def check_permission(user: UserCenter, permission: str) -> None:
        """按需校验权限，供 `require_permission` 的依赖注入覆盖不到的场景用：
        某个权限点要不要求，取决于请求体的内容（例如"只在勾了某个筛选开关时才需要"），
        而不是逐路由的静态声明。逻辑与 `require_permission` 内部一致，独立成静态方法
        是为了不在每个调用点各写一遍同样的判断（PR #66 复审 P1：`competitor_new`
        读取的是竞对私有数据，只挂 `analysis:view`/`analysis:export` 不够）。
        """
        if user.is_super or permission in user.permission_codes:
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"缺少权限：{permission}",
        )


def serialize_user(user: UserCenter) -> dict:
    """当前用户的干净 REST 形状（供 SPA 的 /token、/me 使用）"""
    return {
        "id": user.id,
        "username": user.user_name,
        # 展示名：SSO 账号取自钉钉姓名；本地账号通常为空，前端回落到 username。
        "display_name": user.display_name,
        # 头像：SSO 账号每次登录同步身份源头像；本地账号恒为 None，前端回落到默认图标。
        "avatar_url": user.avatar_url,
        # 账号来源。None = 本地账号（可用密码登录）。
        "external_idp": user.external_idp,
        "role": user.role_code,
        # 展示名与 role 表同源，前端不必自己维护 code → 中文名的映射
        "role_name": user.role_name,
        "permissions": sorted(user.permission_codes),
        "is_active": user.is_active,
        "is_super": user.is_super,
    }


# 全局认证实例
simple_auth = SimpleAuth()
