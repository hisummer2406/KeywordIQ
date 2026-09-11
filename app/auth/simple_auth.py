# app/auth/simple_auth.py
"""简化版认证模块 - 后台登录验证、当前用户与 RBAC 权限判定。

全链路异步（AsyncSession + asyncpg）。bcrypt 是 CPU 密集，由 user_crud 经
run_in_threadpool 移出事件循环——此前 login 在事件循环上直接跑 247 ms 的 bcrypt，
每次登录都让该 worker 停摆同等时长。

role 与 permissions 两个 relationship 都是 lazy="selectin"（预加载），故
AsyncSession 下访问 role_code / permission_codes 不会触发禁止的隐式懒加载。
"""
import logging

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.login_auth import auth_service
from app.common.errors import SessionInvalidError
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

            # 落库先于签发：数据库提交成功后才有可用的登录结果（issue #89）。
            session_id = await crud.rotate_active_session(user)
            token = auth_service.create_access_token(
                user.id, user.user_name, user.is_super, user.role_code, session_id
            )

            return {
                'access_token': token,
                'token_type': 'bearer',
                'expires_in': 24 * 3600,
                'user': serialize_user(user),
            }

    @staticmethod
    async def _decode_and_load_user(
        credentials: HTTPAuthorizationCredentials, db: AsyncSession
    ) -> tuple[dict, UserCenter]:
        """解码令牌、按用户名查用户；令牌无效或用户不存在/已禁用统一 401。
        `get_current_user` 与 `get_current_user_for_logout` 共用这段，差异只在
        要不要额外校验会话仍是当前有效会话（PR #91 复审第六轮）。
        """
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
        return payload, user

    @staticmethod
    async def get_current_user(
            request: Request,
            credentials: HTTPAuthorizationCredentials = Depends(security),
            db: AsyncSession = Depends(get_async_db)
    ) -> UserCenter:
        """获取当前用户（含角色/权限，relationship selectin 一并载入）。

        会话校验（issue #89）：JWT 的 `session_id` 缺失、为空串，或与库中
        `active_session_id` 不一致，均判定为该令牌所属会话已失效，抛
        `SessionInvalidError`（区别于其余令牌无效场景的专属错误码，供前端呈现
        "账号已在其他浏览器登录"）。校验通过后把 `session_id` 挂到
        `request.state`，供需要它的端点使用，不必再解码一次令牌。

        **主动退出不要用这个依赖**，见 `get_current_user_for_logout` 的说明。
        """
        payload, user = await SimpleAuth._decode_and_load_user(credentials, db)

        session_id = payload.get("session_id") or None
        if not session_id or session_id != user.active_session_id:
            raise SessionInvalidError()

        request.state.session_id = session_id
        return user

    @staticmethod
    async def get_current_user_for_logout(
            request: Request,
            credentials: HTTPAuthorizationCredentials = Depends(security),
            db: AsyncSession = Depends(get_async_db)
    ) -> UserCenter:
        """登出专用的身份校验：只验证令牌签名与用户身份，不要求会话仍是当前有效会话
        （PR #91 复审第六轮 P2）。

        主动退出的意图是"确保这张令牌不能再用"——若账号已经在别处重新登录、这张令牌的
        会话早已失效，这本就是退出想要的结果，是 design.md 决策 3 定义的"目标已达成、
        成功的空操作"，不该被当成一次鉴权失败拒绝掉。若这里复用 `get_current_user`，
        它自己的会话校验会先一步抛 `SessionInvalidError`，`logout` 端点里
        `clear_session_if_current` 那段"0 行=已达成"的条件更新因此只在同一个请求内部
        两次数据库往返之间那个极窄窗口才真正生效——"旧会话事后才发起退出请求"这个更
        常见、本该由那段条件更新兜底的场景反而先在这里被拦下，返回一个容易让主动点击
        退出的用户误解的 401（此前的集成测试只直接调 CRUD 方法，没打这条依赖链，没
        测出这个缺口）。

        因此这里只做身份识别，把 JWT 里声称的 `session_id`（可能已经不是当前有效的
        那个，也可能因为是本功能上线前签发的旧令牌而缺失）原样挂到 `request.state`，
        交给 `logout` 端点里的条件更新决定要不要清——不在这一层预先拒绝。**只有
        `logout` 该用它**，其余端点仍必须用 `get_current_user`，否则等于让已经失效
        的会话还能继续访问受保护接口。
        """
        payload, user = await SimpleAuth._decode_and_load_user(credentials, db)
        request.state.session_id = payload.get("session_id") or None
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
    def require_any_permission(*permissions: str):
        """RBAC 权限依赖工厂：持有 ``permissions`` 里任一权限点即放行。

        给跨域共用、不携带任何域私有数据的只读端点用（如关键词排名趋势详情：市场分析屏
        与我的词库屏各自的权限点不同，但端点本身只读一个公共的历史排名，接受任一权限点
        不构成"取并集绕过检查"）。与 :meth:`require_permission` 同构，只是把 AND 换成 OR。
        """

        def _require(current_user: UserCenter = Depends(simple_auth.get_current_user)) -> UserCenter:
            if current_user.is_super or any(
                p in current_user.permission_codes for p in permissions
            ):
                return current_user
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"缺少权限：{' 或 '.join(permissions)}",
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
