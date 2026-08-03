import logging
from datetime import datetime
from typing import Any

from fastapi.concurrency import run_in_threadpool
from passlib.context import CryptContext
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import DEFAULT_ROLE_FOR_NON_SUPER, DEFAULT_ROLE_FOR_SUPER
from app.user.rbac_model import Role
from app.user.user_errors import (
    DuplicateUserNameError,
    SuperUserDeactivationError,
    UserNotFoundError,
)
from app.user.user_model import UserCenter
from app.user.user_schemas import UserCenterCreate, UserCenterList, UserCenterUpdate

logger = logging.getLogger(__name__)

# 密码加密上下文
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class UserCenterCRUD:
    """用户中心 CRUD，含业务校验。

    **失败一律「抛」不「返」。** 早先写方法返回 ``(success, message, user)`` 元组、
    读方法用 ``except Exception: return []`` 吞异常，两者都有害：

    - 返值式失败要靠每个调用点自觉检查，漏检即静默；API 层为此按中文子串猜状态码
      （``"不存在" in message`` → 404），改一个字的文案就会静默降级。
    - 吞掉读异常更糟：全局处理器本会把 ``OperationalError`` 转成 503 并记栈，
      吞掉之后数据库挂了却返回空列表——「查询失败」与「没有用户」再也分不开。

    数据库异常不在这里捕获，直接上浮交全局处理器（503/500 + 栈）。写方法只做
    「回滚后重抛」，不改变异常本身。
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_users_paginated(
            self,
            page: int = 1,
            per_page: int = 20,
            user_name: str | None = None,
            is_active: bool | None = None
    ) -> dict[str, Any]:
        """分页获取用户列表。

        查询失败不再压成空结果——那会让「数据库挂了」在界面上长得和「没有用户」
        一模一样，且不产生任何告警。异常上浮由全局处理器转成 503/500。
        """
        skip = (page - 1) * per_page
        users = await self.get_users_list(
            skip=skip, limit=per_page, user_name=user_name, is_active=is_active
        )
        total = await self.count_users(search=user_name, is_active=is_active)

        return {
            "items": [UserCenterList.model_validate(user) for user in users],
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page,
        }

    async def _default_role_id(self, is_super: bool) -> int | None:
        """按 is_super 取默认角色 id（admin / operator），与迁移回填一致。"""
        code = DEFAULT_ROLE_FOR_SUPER if is_super else DEFAULT_ROLE_FOR_NON_SUPER
        return (await self.db.execute(select(Role.id).where(Role.code == code))).scalar_one_or_none()

    async def get_user_by_id(self, user_id: int) -> UserCenter | None:
        """根据 ID 获取用户。不存在返回 None；查询失败上抛。"""
        stmt = select(UserCenter).where(UserCenter.id == user_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_user_by_username(self, username: str) -> UserCenter | None:
        """根据用户名获取用户。不存在返回 None；查询失败上抛。

        ``None`` 只表示「查过了，没有」。此前查询异常也返回 ``None``，于是数据库
        故障会被当成「用户名可用」——创建用户时正好走进去。
        """
        stmt = select(UserCenter).where(UserCenter.user_name == username)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_users_list(
            self,
            skip: int = 0,
            limit: int = 100,
            user_name: str | None = None,
            is_active: bool | None = None
    ) -> list[UserCenter]:
        """获取用户列表。空列表只表示「没有匹配」，查询失败上抛。"""
        stmt = select(UserCenter)

        if user_name:
            stmt = stmt.where(UserCenter.user_name.ilike(f"%{user_name}%"))
        if is_active is not None:
            stmt = stmt.where(UserCenter.is_active == is_active)

        stmt = stmt.order_by(desc(UserCenter.created_at)).offset(skip).limit(limit)
        return list((await self.db.execute(stmt)).scalars().all())

    async def count_users(
            self,
            search: str | None = None,
            is_active: bool | None = None
    ) -> int:
        """统计用户总数。0 只表示「没有匹配」，查询失败上抛。"""
        stmt = select(func.count(UserCenter.id))

        if search:
            stmt = stmt.where(UserCenter.user_name.ilike(f"%{search}%"))
        if is_active is not None:
            stmt = stmt.where(UserCenter.is_active == is_active)

        return (await self.db.execute(stmt)).scalar() or 0

    async def create_user(self, user_data: UserCenterCreate) -> UserCenter:
        """创建用户。用户名重复抛 :class:`DuplicateUserNameError`（409）。"""
        if await self.get_user_by_username(user_data.user_name):
            raise DuplicateUserNameError()

        # 解析角色：显式指定优先，否则按 is_super 赋默认角色（避免无角色→无权限）
        role_id = user_data.role_id or await self._default_role_id(user_data.is_super)

        db_user = UserCenter(
            user_name=user_data.user_name,
            hashed_pwd=await self.hash_password(user_data.password),
            is_active=user_data.is_active,
            is_super=user_data.is_super,
            role_id=role_id,
        )

        try:
            self.db.add(db_user)
            await self.db.commit()
            await self.db.refresh(db_user)
        except Exception:
            # 只回滚，不改变异常——数据库故障该由全局处理器判成 503 并留栈。
            await self.db.rollback()
            raise

        logger.info("用户创建成功: %s - %s", db_user.id, db_user.user_name)
        return db_user

    async def update_user(
            self,
            user_id: int,
            user_data: UserCenterUpdate
    ) -> UserCenter:
        """更新用户。不存在抛 404，用户名与他人冲突抛 409。"""
        db_user = await self.get_user_by_id(user_id)
        if not db_user:
            raise UserNotFoundError()

        if user_data.user_name and user_data.user_name != db_user.user_name:
            if await self.get_user_by_username(user_data.user_name):
                raise DuplicateUserNameError("用户名已被其他用户使用")

        update_data = user_data.model_dump(exclude_unset=True)
        if "password" in update_data:
            update_data["hashed_pwd"] = await self.hash_password(update_data.pop("password"))

        for key, value in update_data.items():
            if hasattr(db_user, key):
                setattr(db_user, key, value)
        db_user.updated_at = datetime.now()

        try:
            await self.db.commit()
            await self.db.refresh(db_user)
        except Exception:
            await self.db.rollback()
            raise

        logger.info("用户更新成功: %s", user_id)
        return db_user

    async def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """验证密码。**bcrypt 经线程池执行，不占事件循环。**

        实测单次 verify 约 247 ms 纯 CPU。异步化解决不了这个——没有可等待的 I/O，
        只有计算。直接在事件循环上调用会让该 worker 停摆同等时长，集中登录时段可被
        用户感知，故必须移出去。慢本身是 bcrypt 的设计目的（抵抗离线爆破），调低
        rounds 是拿安全换延迟，不做。

        捕获并失败关闭（返回 False）是**刻意**的：哈希串损坏或算法不识别时，正确的
        结果就是「验不过」，不该变成 500 把细节暴露给未认证的调用方。但要用
        exception 记栈——否则一批用户突然登不上时无从查起。
        """
        try:
            return await run_in_threadpool(pwd_context.verify, plain_password, hashed_password)
        except Exception:
            logger.exception("密码验证异常，按验证失败处理")
            return False

    async def hash_password(self, password: str) -> str:
        """加密密码。同样经线程池（约 249 ms 纯 CPU）。失败上抛——加密不出来就不该继续建号。"""
        return await run_in_threadpool(pwd_context.hash, password)

    async def authenticate_user(self, username: str, password: str) -> UserCenter | None:
        """用户认证。凭证不符返回 ``None``；数据库故障上抛。

        此前查询异常也被压成 ``None``，与「密码错误」无从区分——数据库挂了会表现为
        全站密码错误，而不是 503。
        """
        user = await self.get_user_by_username(username)
        if not user or not user.is_active:
            return None
        if not await self.verify_password(password, user.hashed_pwd):
            return None
        return user

    async def toggle_user_status(self, user_id: int) -> UserCenter:
        """切换用户状态（激活/禁用）。不存在抛 404，禁用超管抛 409。"""
        user = await self.get_user_by_id(user_id)
        if not user:
            raise UserNotFoundError()

        if user.is_super and user.is_active:
            raise SuperUserDeactivationError()

        updated_user = await self.update_user(user_id, UserCenterUpdate(is_active=not user.is_active))

        logger.info(
            "用户状态切换成功: %s -> %s", user_id, "激活" if updated_user.is_active else "禁用"
        )
        return updated_user
