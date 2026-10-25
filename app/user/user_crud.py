import logging
import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi.concurrency import run_in_threadpool
from passlib.context import CryptContext
from sqlalchemy import desc, func, select, update
from sqlalchemy.exc import IntegrityError
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

# PostgreSQL 的唯一约束冲突
_UNIQUE_VIOLATION = "23505"

# 用户不存在时拿它跑一遍校验，把登录耗时拉平（见 authenticate_user）。
# 是一个随机口令的真实 bcrypt 哈希，与任何账号都不关联，验不过是必然结果——
# 要的只是那 ~250 ms 的计算量。写死而非启动时现算：现算会让每个进程的启动多花
# 一次哈希时间，而这个值没有任何需要保密或轮换的理由。
_DUMMY_PASSWORD_HASH = "$2b$12$5twyKQRPYjp.3gGB8BeVs.d6nksEpKBc09B0jnqg/d9.qVsIbsSCm"


def _reraise_duplicate_name(exc: Exception, message: str | None = None) -> None:
    """写入撞上唯一约束时改抛 409；其余异常原样交回调用方继续上抛。

    先查后插挡不住并发——两个请求可以同时查到「没有同名用户」。真正的裁决者是库里
    ``user_center.user_name`` 上的唯一约束，而约束冲突若原样上浮，全局的
    ``SQLAlchemyError`` 处理器会把它判成 500。这是调用方能自己纠正的输入问题，
    不是服务端故障。

    只认 SQLSTATE 23505：别的完整性错误（如 role_id 外键失效）不能被冒充成
    「用户名已存在」——那会把一个配置问题伪装成用户操作问题。错误码位置随驱动而异，
    asyncpg 叫 ``sqlstate``，psycopg2 叫 ``pgcode``。
    """
    if not isinstance(exc, IntegrityError):
        return
    orig = getattr(exc, "orig", None)
    code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if code == _UNIQUE_VIOLATION:
        raise DuplicateUserNameError(message) if message else DuplicateUserNameError()


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
        except Exception as exc:
            # 只回滚，不改变异常——数据库故障该由全局处理器判成 503 并留栈。
            # 唯一例外是唯一约束冲突：那是调用方能自己纠正的输入问题，见下。
            await self.db.rollback()
            _reraise_duplicate_name(exc)
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
        db_user.updated_at = datetime.now(UTC)  # 带时区，原因见 import_service 的 completed_at

        try:
            await self.db.commit()
            await self.db.refresh(db_user)
        except Exception as exc:
            await self.db.rollback()
            _reraise_duplicate_name(exc, "用户名已被其他用户使用")
            raise

        logger.info("用户更新成功: %s", user_id)
        return db_user

    # ---------- 外部身份（SSO） ----------

    async def get_by_external_identity(self, idp: str, external_id: str) -> UserCenter | None:
        """按 ``(external_idp, external_id)`` 取账号。

        **账号匹配只认这一对。** 绝不按用户名匹配——若按用户名自动绑定已有账号，任何能
        修改自己钉钉 userid 的人都可以把它改成 ``admin``，扫一次码就接管超级管理员。
        """
        return await self.db.scalar(
            select(UserCenter).where(
                UserCenter.external_idp == idp,
                UserCenter.external_id == external_id,
            )
        )

    async def list_user_names(self) -> set[str]:
        """已占用的用户名集合，供 SSO 建档时避让冲突。

        用户表是几十到几百行的量级，一次全取比逐个试探省事。真到了万级再改成按前缀查。
        """
        return set(await self.db.scalars(select(UserCenter.user_name)))

    async def create_sso_user(
        self,
        *,
        user_name: str,
        display_name: str | None,
        avatar_url: str | None,
        idp: str,
        external_id: str,
        role_code: str,
    ) -> UserCenter:
        """建立一个外部身份账号。

        三条不可动摇：``hashed_pwd`` 为 NULL（没有密码是**事实**，不是"永不匹配的散列"
        那条隐含约定）；``is_super`` **恒假**（用户管理是不可授予的能力，由 require_super
        把守，不能由外部身份源决定谁拥有它）；角色取配置的默认值，**不从钉钉部门映射**
        ——那会让"谁能导入数据"取决于通讯录怎么编排。
        """
        role_id = await self.db.scalar(select(Role.id).where(Role.code == role_code))
        if role_id is None:
            # 启动期检查本该拦住这种情况；真走到这里说明角色被删了，宁可报错也不要
            # 建出一个无角色、进任何页面都 403 的空壳账号。
            raise ValueError(f"SSO 默认角色不存在: {role_code}")

        user = UserCenter(
            user_name=user_name,
            display_name=display_name or None,
            avatar_url=avatar_url or None,
            hashed_pwd=None,
            is_active=True,
            is_super=False,
            role_id=role_id,
            external_idp=idp,
            external_id=external_id,
        )
        try:
            self.db.add(user)
            await self.db.commit()
            await self.db.refresh(user)
        except Exception as exc:
            await self.db.rollback()
            _reraise_duplicate_name(exc)
            raise

        logger.info("SSO 建档: id=%s user_name=%s idp=%s", user.id, user.user_name, idp)
        return user

    async def sync_avatar(self, user: UserCenter, avatar_url: str | None) -> UserCenter:
        """已建档账号登录时刷新头像。

        **与"已建档账号不回写任何属性"这条既有规则刻意不同**：那条规则是为 display_name /
        user_name 定的，理由是回写要处理用户名唯一约束的连锁影响；头像没有唯一约束顾虑，
        且头像换了却在系统里显示旧照片，比显示名滞后更容易让人觉得"认错人"。因此头像
        每次登录都同步，其余字段仍然不动。

        跳过无变化写入（``IS DISTINCT FROM`` 同一条纪律）：头像地址多数登录不变，
        无谓的 UPDATE 只会产生死元组。
        """
        if avatar_url == user.avatar_url:
            return user
        user.avatar_url = avatar_url or None
        await self.db.commit()
        await self.db.refresh(user)
        return user

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

        **无论用户是否存在都跑一遍 bcrypt。** 早前的写法在查不到用户时直接返回，
        那条路径是毫秒级，而正常路径要 247 ms——三个数量级的差距，不需要统计手段，
        肉眼看响应时间就能把有效用户名逐个试出来。对不存在/已禁用的账号改用一个
        固定的哑哈希跑校验，两条路径耗时同阶。
        """
        user = await self.get_user_by_username(username)
        # `user.hashed_pwd` 为 NULL 的是 SSO 账号（钉钉扫码建立，本就没有密码）。
        # 它同样要跑完哑哈希，**不能提前返回**：提前返回把这条路径压到毫秒级，
        # 「这个用户名是不是 SSO 账号」就能靠响应耗时逐个试出来——正是这个哑哈希
        # 当初要堵的那条侧信道。顺带也避免 passlib 拿 None 去 verify 时抛异常刷日志。
        target_hash = user.hashed_pwd if user and user.hashed_pwd else _DUMMY_PASSWORD_HASH
        password_ok = await self.verify_password(password, target_hash)

        if not user or not user.is_active or not password_ok:
            return None
        return user

    async def rotate_active_session(self, user: UserCenter) -> str:
        """登录成功后生成新会话编号并覆盖旧的，使旧会话对应的令牌立即失效（issue #89）。

        **本地密码登录与钉钉扫码登录共用这一个方法**，不各自实现一遍——两处若各写一份，
        任何一处日后调整（比如换一种编号生成方式）都不会带着另一处一起变，产生的行为
        分歧没有任何地方会报错。

        只应在认证/建档成功之后调用：失败的登录尝试不得替换有效会话，本方法不做这层
        判断，由调用方保证只在成功路径上调它。

        **返回值取本地变量，不取 `refresh` 之后的 `user.active_session_id`。** 并发场景
        下，`commit()` 与 `refresh()` 之间存在一次真实的事件循环让出——若另一个并发登录
        恰好在这个间隙提交，`refresh()` 读回的会是**那一次**的值而不是本次自己刚写入的，
        调用方就会拿着一个签发时看似成功、实际早已不是当前有效会话的编号去签发令牌。
        `refresh()` 仍然保留（让 `user` 对象其余字段与库同步，供调用方继续使用），只是
        不作为这个方法返回值的来源。
        """
        session_id = secrets.token_urlsafe(32)
        user.active_session_id = session_id
        await self.db.commit()
        await self.db.refresh(user)
        return session_id

    async def clear_session_if_current(self, user_id: int, session_id: str) -> None:
        """主动退出：仅当库里的有效会话仍等于本次令牌的会话编号时才清空。

        不判断受影响行数——0 行（已被更晚的登录替换）与 1 行（确实清掉了）都是
        "目标状态已达成"，不是错误。这正是"旧会话的退出请求与新登录并发时,不能注销
        新会话"的落点：旧请求的 WHERE 条件在新登录写入之后必然匹配不到行，天然是空操作。
        """
        await self.db.execute(
            update(UserCenter)
            .where(UserCenter.id == user_id, UserCenter.active_session_id == session_id)
            .values(active_session_id=None)
        )
        await self.db.commit()

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
