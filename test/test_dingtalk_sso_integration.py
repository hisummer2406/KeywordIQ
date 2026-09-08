"""钉钉单点登录的数据库集成测试（真库 + asyncpg）。

这里验的是**账号匹配与建档规则**——单元测试碰不到它，因为核心断言全都关于唯一约束、
并发下的行归属、以及"这一次登录到底命中了哪一行"。

必须打异步链路：登录路径走 ``AsyncSession``/asyncpg。

钉钉的 HTTP **一律用假客户端顶掉，不打真网**。客户端本身的行为（超时映射、企业凭证
缓存与并发保护、errcode 判读）由 `test_dingtalk_sso_unit.py` 用 `httpx.MockTransport`
覆盖；这里要验的是它之上的那层逻辑。

默认跳过，只在设置 ``TEST_DATABASE_URL`` 时运行。每个测试独占事务、结束回滚。

    TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run pytest test/test_dingtalk_sso_integration.py -v
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth import sso_service
from app.auth.sso_errors import SsoAccountDisabledError, SsoNotEmployeeError
from config import settings
from test.test_site_scoped_query_integration import _async_url

TEST_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 运行钉钉 SSO 集成测试"
)

# 本文件造的账号统一带这个前缀，断言一律按它限定作用域。
# 裸 `count(*) FROM user_center` 只在"库里除了我什么都没有"时成立，而 keywordiq_test
# 是全局共享的一份，别的会话留下的已提交数据会让断言失败，且报出来的 diff 完全看不出
# 是这个原因。
PREFIX = "ssotest"


@pytest.fixture()
async def db():
    engine = create_async_engine(_async_url(TEST_URL), poolclass=NullPool)
    connection = await engine.connect()
    trans = await connection.begin()
    # create_savepoint 是必须的：`create_sso_user` 内部 commit（与 `create_user` 同一形态）。
    # 不挂 savepoint 的话那次 commit 会提交到外层事务之外，测试数据留在共享库里回滚不掉。
    session = AsyncSession(bind=connection, join_transaction_mode="create_savepoint")

    async def _bail(reason: str):
        await session.close()
        await trans.rollback()
        await connection.close()
        await engine.dispose()
        pytest.skip(reason)

    # 测试库跑没跑过本变更的迁移。**这道闸不能省**：共享的 keywordiq_test 由所有并行会话
    # 共用，很可能停在别的 head 上；没有它，缺列会以 UndefinedColumnError 的形式**硬失败**，
    # 而那是「环境没准备好」不是「代码错了」——两者在报错信息里长得完全不一样，却会一起
    # 把提交门禁拦住。仓库里其他集成测试都用同一形态（见 fact_growth 那几处）。
    has_column = await session.scalar(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'analysis' AND table_name = 'user_center' "
            "AND column_name = 'external_id'"
        )
    )
    if not has_column:
        await _bail("测试库未迁移到含 user_center.external_id 的版本，请先 alembic upgrade head")

    role_id = await session.scalar(
        text("SELECT id FROM analysis.role WHERE code = :c"),
        {"c": settings.SSO_DEFAULT_ROLE_CODE},
    )
    if role_id is None:
        await _bail(f"测试库缺少角色 {settings.SSO_DEFAULT_ROLE_CODE}，请先 alembic upgrade head")

    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await connection.close()
        await engine.dispose()


class FakeDingTalk:
    """假的钉钉客户端。`directory` 为 unionId → (userid, 姓名) 的映射；不在其中即"不在通讯录内"。"""

    def __init__(
        self,
        union_id: str,
        directory: dict[str, tuple[str, str]],
        avatar_url: str | None = None,
    ):
        self._union_id = union_id
        self._directory = directory
        self._avatar_url = avatar_url
        self.calls: list[str] = []

    async def exchange_user_token(self, auth_code: str) -> str:
        self.calls.append("exchange_user_token")
        return "user-token"

    async def contact_me(self, user_access_token: str) -> dict:
        self.calls.append("contact_me")
        return {"unionId": self._union_id, "nick": "某人", "avatarUrl": self._avatar_url}

    async def corp_access_token(self) -> str:
        self.calls.append("corp_access_token")
        return "corp-token"

    async def userid_by_unionid(self, corp_token: str, unionid: str) -> str | None:
        self.calls.append("userid_by_unionid")
        entry = self._directory.get(unionid)
        return entry[0] if entry else None

    async def user_detail(self, corp_token: str, userid: str) -> dict:
        self.calls.append("user_detail")
        for uid, name in self._directory.values():
            if uid == userid:
                return {"name": name}
        return {}


async def _login(db: AsyncSession, client: FakeDingTalk) -> dict:
    return await sso_service.login_with_auth_code(
        db, client, auth_code="code", state=sso_service.issue_state()
    )


async def _count_scoped(db: AsyncSession, external_id: str) -> int:
    """按外部身份限定作用域计数，不用裸 count(*)（共享测试库）。"""
    return await db.scalar(
        text(
            "SELECT count(*) FROM analysis.user_center "
            "WHERE external_idp = 'dingtalk' AND external_id = :eid"
        ),
        {"eid": external_id},
    )


# ---------------- 6.1 建档与复用 ----------------

async def test_首次扫码建档并赋默认角色(db):
    union = f"{PREFIX}-union-1"
    client = FakeDingTalk(union, {union: (f"{PREFIX}First", "张三")})

    result = await _login(db, client)
    user = result["user"]

    assert user.external_idp == "dingtalk"
    assert user.external_id == union
    assert user.user_name == f"{PREFIX}first"  # 规范化：转小写、剥非字母数字
    assert user.display_name == "张三"
    assert user.hashed_pwd is None
    assert user.is_super is False
    assert user.role_code == settings.SSO_DEFAULT_ROLE_CODE
    assert result["token"]


async def test_二次扫码复用同一行不重复建档(db):
    union = f"{PREFIX}-union-2"
    client = FakeDingTalk(union, {union: (f"{PREFIX}Second", "李四")})

    first = await _login(db, client)
    second = await _login(db, client)

    assert first["user"].id == second["user"].id
    assert await _count_scoped(db, union) == 1


# ---------------- 头像：与 display_name 刻意不同，每次登录都刷新 ----------------

async def test_建档时写入头像(db):
    union = f"{PREFIX}-union-avatar-1"
    client = FakeDingTalk(
        union, {union: (f"{PREFIX}Avatar1", "头像甲")}, avatar_url="https://static.dingtalk.com/a.png"
    )

    result = await _login(db, client)

    assert result["user"].avatar_url == "https://static.dingtalk.com/a.png"


async def test_二次登录头像变化会刷新_不同于display_name的不回写(db):
    union = f"{PREFIX}-union-avatar-2"
    client = FakeDingTalk(
        union, {union: (f"{PREFIX}Avatar2", "头像乙")}, avatar_url="https://static.dingtalk.com/old.png"
    )
    first = await _login(db, client)
    assert first["user"].avatar_url == "https://static.dingtalk.com/old.png"

    changed = FakeDingTalk(
        union, {union: (f"{PREFIX}Avatar2", "头像乙")}, avatar_url="https://static.dingtalk.com/new.png"
    )
    second = await _login(db, changed)

    assert second["user"].id == first["user"].id
    assert second["user"].avatar_url == "https://static.dingtalk.com/new.png"


# ---------------- 6.2 userid 变更不影响归属 ----------------

async def test_企业userid变更后仍命中原账号(db):
    """external_id 存的是 unionId，不是 userid。

    存 userid 的话，管理员改一次工号就让老账号成孤儿、当事人被当新人重新开号、
    已配置的角色全部丢失——而这一切不会有任何报错。
    """
    union = f"{PREFIX}-union-3"
    client = FakeDingTalk(union, {union: (f"{PREFIX}Old", "王五")})
    first = await _login(db, client)

    # 钉钉侧改了工号，unionId 不变
    changed = FakeDingTalk(union, {union: (f"{PREFIX}New", "王五")})
    second = await _login(db, changed)

    assert second["user"].id == first["user"].id
    assert second["user"].user_name == f"{PREFIX}old"  # 不回写，沿用建档时的名字
    assert await _count_scoped(db, union) == 1


# ---------------- 6.3 用户名撞车不构成绑定 ----------------

async def test_用户名与本地账号相同时绝不登进那个账号(db):
    """本功能唯一实质的攻击面。

    若按用户名自动绑定，任何能改自己钉钉 userid 的人都能把它改成本地某个账号的用户名
    （比如 admin），扫一次码就接管它。用户名撞了只说明重名，**不说明是同一个人**。
    """
    local_name = f"{PREFIX}collide"
    local_id = await db.scalar(
        text(
            "INSERT INTO analysis.user_center(user_name, hashed_pwd, is_active, is_super) "
            "VALUES (:n, 'x', true, true) RETURNING id"
        ),
        {"n": local_name},
    )

    union = f"{PREFIX}-union-4"
    client = FakeDingTalk(union, {union: (local_name, "冒名者")})
    result = await _login(db, client)

    assert result["user"].id != local_id, "绝不能登进那个本地账号"
    assert result["user"].user_name == f"{local_name}2", "撞名就换一个"
    assert result["user"].is_super is False, "更不能继承它的超管位"


# ---------------- 6.4 拒登路径 ----------------

async def test_不在通讯录内即拒登(db):
    """离职同步就落在这里：钉钉侧移出通讯录后，反查拿不到 userid。"""
    union = f"{PREFIX}-union-5"
    client = FakeDingTalk(union, {})  # 通讯录里没有他

    with pytest.raises(SsoNotEmployeeError):
        await _login(db, client)

    assert await _count_scoped(db, union) == 0, "拒登就不该留下账号"


async def test_已停用账号拒登且不签发令牌(db):
    union = f"{PREFIX}-union-6"
    client = FakeDingTalk(union, {union: (f"{PREFIX}Disabled", "赵六")})
    created = await _login(db, client)

    await db.execute(
        text("UPDATE analysis.user_center SET is_active = false WHERE id = :i"),
        {"i": created["user"].id},
    )
    await db.commit()
    db.expire_all()

    with pytest.raises(SsoAccountDisabledError):
        await _login(db, client)


async def test_停用判定发生在建档之后而非把人挡在门外(db):
    """停用的是账号，不是身份——被停用的人再扫码不该又建出一个新账号绕过停用。"""
    union = f"{PREFIX}-union-7"
    client = FakeDingTalk(union, {union: (f"{PREFIX}Seven", "钱七")})
    await _login(db, client)

    await db.execute(
        text("UPDATE analysis.user_center SET is_active = false "
             "WHERE external_id = :e AND external_idp = 'dingtalk'"),
        {"e": union},
    )
    await db.commit()
    db.expire_all()

    with pytest.raises(SsoAccountDisabledError):
        await _login(db, client)

    assert await _count_scoped(db, union) == 1, "不得绕过停用另建账号"
