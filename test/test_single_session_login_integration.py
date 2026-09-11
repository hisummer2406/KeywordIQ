"""登录会话唯一性的数据库集成测试（issue #89，真库 + asyncpg）。

验的是数据库层面的语义：会话替换、条件退出、并发写入的最终一致性、`get_current_user`
对真实持久化的 `active_session_id` 的校验。控制流层面的分支（缺失/空/不匹配三态都抛
`SessionInvalidError`）已由 `test_rbac_auth_unit.py` 用 monkeypatch 覆盖，这里不重复。

**直接调用 `UserCenterCRUD` 的方法与 `simple_auth.get_current_user`，不打
`simple_auth.login`/`POST /api/auth/logout` 这两层薄编排**：`simple_auth.login` 内部用
`AsyncSessionFactory()` 自建会话，绑定的是 `.env` 的 `DATABASE_URL_ASYNC`，与本文件测试
夹具持有的 `TEST_DATABASE_URL` 连接是两个不同的数据库，测试断言不到自己刚插入的数据。
与 `test_dingtalk_sso_integration.py` 直接调 `login_with_auth_code` 而不打 HTTP 端点是
同一个理由——真正要验的逻辑在被编排的那一层。

默认跳过，只在设置 ``TEST_DATABASE_URL`` 时运行：

    TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run pytest test/test_single_session_login_integration.py -v
"""

from __future__ import annotations

import asyncio
import os
import types

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth import sso_service
from app.auth.login_auth import auth_service
from app.auth.simple_auth import simple_auth
from app.common.errors import SessionInvalidError
from app.user.user_crud import UserCenterCRUD
from app.user.user_schemas import UserCenterCreate
from test.test_dingtalk_sso_integration import FakeDingTalk
from test.test_site_scoped_query_integration import _async_url

TEST_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 运行登录会话唯一性集成测试"
)

# 本文件造的账号统一带这个前缀，断言一律按它限定作用域（keywordiq_test 是共享库）。
PREFIX = "sesstest"


@pytest.fixture()
async def db():
    engine = create_async_engine(_async_url(TEST_URL), poolclass=NullPool)
    connection = await engine.connect()
    trans = await connection.begin()
    # create_savepoint：本文件用到的 CRUD 方法（create_user/rotate_active_session/
    # clear_session_if_current）内部都会 commit，不挂 savepoint 的话那些 commit 会
    # 提交到外层事务之外，测试数据留在共享库里回滚不掉（与 sso 集成测试同一形态）。
    session = AsyncSession(bind=connection, join_transaction_mode="create_savepoint")

    async def _bail(reason: str):
        await session.close()
        await trans.rollback()
        await connection.close()
        await engine.dispose()
        pytest.skip(reason)

    has_column = await session.scalar(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'analysis' AND table_name = 'user_center' "
            "AND column_name = 'active_session_id'"
        )
    )
    if not has_column:
        await _bail(
            "测试库未迁移到含 user_center.active_session_id 的版本，请先 alembic upgrade head"
        )

    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await connection.close()
        await engine.dispose()


def _fake_request():
    return types.SimpleNamespace(state=types.SimpleNamespace())


async def _create_local_user(db: AsyncSession, suffix: str):
    return await UserCenterCRUD(db).create_user(
        UserCenterCreate(user_name=f"{PREFIX}{suffix}", password="pwd123456")
    )


async def _authorize(db: AsyncSession, token: str):
    """按真实鉴权路径跑一遍（`get_current_user` 自行解码令牌）。会话不合法时抛
    `SessionInvalidError`；合法时返回对应的 `UserCenter`。"""
    return await simple_auth.get_current_user(
        request=_fake_request(),
        credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
        db=db,
    )


# ---------------- 登录替换旧会话 ----------------


async def test_登录成功替换旧会话_旧令牌立即失效(db):
    user = await _create_local_user(db, "replace")
    crud = UserCenterCRUD(db)

    old_session = await crud.rotate_active_session(user)
    old_token = auth_service.create_access_token(
        user.id, user.user_name, user.is_super, user.role_code, old_session
    )
    assert (await _authorize(db, old_token)).id == user.id  # 旧令牌此刻仍有效

    new_session = await crud.rotate_active_session(user)
    assert new_session != old_session

    with pytest.raises(SessionInvalidError):
        await _authorize(db, old_token)  # 旧令牌被顶替后立即失效

    new_token = auth_service.create_access_token(
        user.id, user.user_name, user.is_super, user.role_code, new_session
    )
    assert (await _authorize(db, new_token)).id == user.id


async def test_登录失败不影响已有会话(db):
    user = await _create_local_user(db, "keepsession")
    crud = UserCenterCRUD(db)
    session_id = await crud.rotate_active_session(user)

    # 密码错误：authenticate_user 返回 None，不触达 rotate_active_session
    failed = await crud.authenticate_user(user.user_name, "wrong-password")
    assert failed is None

    await db.refresh(user)
    assert user.active_session_id == session_id, "登录失败不得改变已有会话"


# ---------------- 并发登录：最终只有一次写入生效 ----------------


async def test_并发登录只有最后提交的一次生效():
    """两个独立连接（真实并发写入）同时对同一账号登录。

    **不使用共享的 `db` 夹具**：那个夹具靠 SAVEPOINT 隔离、测试结束整体回滚，两个
    连接看不到彼此未提交的写入，做不出真实并发。这里改为真实 `commit` + 显式清理
    （`finally` 里 DELETE），是本文件唯一一处这样写的测试。

    不断言"谁先谁后"（那由数据库调度决定，本变更刻意不引入应用层锁来控制顺序，
    见 design.md 决策 5）；只断言最终状态自洽：恰好一个会话编号生效，另一个立即失效。
    """
    engine = create_async_engine(_async_url(TEST_URL), poolclass=NullPool)
    user_id = None
    try:
        async with AsyncSession(engine) as setup:
            user = await UserCenterCRUD(setup).create_user(
                UserCenterCreate(user_name=f"{PREFIX}concurrent", password="pwd123456")
            )
            user_id = user.id
            user_name, is_super, role_code = user.user_name, user.is_super, user.role_code

        async def _login_via_own_connection() -> str:
            async with AsyncSession(engine) as session:
                u = await UserCenterCRUD(session).get_user_by_id(user_id)
                return await UserCenterCRUD(session).rotate_active_session(u)

        session_a, session_b = await asyncio.gather(
            _login_via_own_connection(), _login_via_own_connection()
        )
        assert session_a != session_b

        async with AsyncSession(engine) as check:
            final = await check.scalar(
                text("SELECT active_session_id FROM analysis.user_center WHERE id = :i"),
                {"i": user_id},
            )
            assert final in (session_a, session_b)

            winner_token = auth_service.create_access_token(
                user_id, user_name, is_super, role_code, final
            )
            assert (await _authorize(check, winner_token)).id == user_id

            loser = session_b if final == session_a else session_a
            loser_token = auth_service.create_access_token(
                user_id, user_name, is_super, role_code, loser
            )
            with pytest.raises(SessionInvalidError):
                await _authorize(check, loser_token)
    finally:
        if user_id is not None:
            async with AsyncSession(engine) as cleanup:
                await cleanup.execute(
                    text("DELETE FROM analysis.user_center WHERE id = :i"), {"i": user_id}
                )
                await cleanup.commit()
        await engine.dispose()


# ---------------- 主动退出：条件清除 ----------------


async def test_主动退出清除当前会话(db):
    user = await _create_local_user(db, "logout")
    crud = UserCenterCRUD(db)
    session_id = await crud.rotate_active_session(user)

    await crud.clear_session_if_current(user.id, session_id)

    await db.refresh(user)
    assert user.active_session_id is None

    token = auth_service.create_access_token(
        user.id, user.user_name, user.is_super, user.role_code, session_id
    )
    with pytest.raises(SessionInvalidError):
        await _authorize(db, token)


async def test_旧会话退出请求与新登录并发时不清除新会话(db):
    user = await _create_local_user(db, "stalelogout")
    crud = UserCenterCRUD(db)
    old_session = await crud.rotate_active_session(user)

    # 旧会话的退出请求"迟到"，此时账号已经被新登录顶替
    new_session = await crud.rotate_active_session(user)
    await crud.clear_session_if_current(user.id, old_session)

    await db.refresh(user)
    assert user.active_session_id == new_session, "旧会话的退出请求不得清除新会话"

    new_token = auth_service.create_access_token(
        user.id, user.user_name, user.is_super, user.role_code, new_session
    )
    assert (await _authorize(db, new_token)).id == user.id


async def test_旧会话退出请求打真实依赖链不再被提前拒绝(db):
    """PR #91 复审第六轮 P2 的回归：上面那条"不清除新会话"的用例只直接调
    `clear_session_if_current`，绕开了 `get_current_user`/`get_current_user_for_logout`
    这层依赖链——而真实 HTTP 请求必须先过依赖链才能摸到端点里的条件更新。这里改为
    打通整条链路：先用旧令牌走 `get_current_user_for_logout`（不是 `get_current_user`，
    后者会在这一步就因为会话不匹配直接抛 `SessionInvalidError`，永远走不到端点），
    再调用 `logout` 端点函数本身，断言新会话全须全尾、旧会话的这次退出请求本身成功
    （不是 401）。
    """
    from app.auth.auth_api import logout as logout_endpoint

    user = await _create_local_user(db, "realchainlogout")
    crud = UserCenterCRUD(db)
    old_session = await crud.rotate_active_session(user)
    old_token = auth_service.create_access_token(
        user.id, user.user_name, user.is_super, user.role_code, old_session
    )

    # 旧会话的退出请求"迟到"：在它摸到鉴权依赖之前，账号已经被新登录顶替
    new_session = await crud.rotate_active_session(user)

    request = _fake_request()
    current_user = await simple_auth.get_current_user_for_logout(
        request=request,
        credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=old_token),
        db=db,
    )  # 不应该抛 SessionInvalidError——这正是本轮要修的那个缺口

    result = await logout_endpoint(request=request, current_user=current_user, db=db)
    assert result == {"success": True}

    await db.refresh(user)
    assert user.active_session_id == new_session, "旧会话事后才发起的退出请求不得清除新会话"

    new_token = auth_service.create_access_token(
        user.id, user.user_name, user.is_super, user.role_code, new_session
    )
    assert (await _authorize(db, new_token)).id == user.id  # 新会话不受影响，仍然有效


async def test_退出条件不满足时视为已达成不报错(db):
    user = await _create_local_user(db, "idempotentlogout")
    crud = UserCenterCRUD(db)
    await crud.rotate_active_session(user)

    # session_id 对不上（此处甚至从未被签发过），条件更新应是空操作而非报错
    await crud.clear_session_if_current(user.id, "never-issued-session-id")


# ---------------- 钉钉扫码登录共用同一套会话签发 ----------------


async def test_钉钉扫码登录同样触发会话替换(db):
    union = f"{PREFIX}-ding-union"
    client = FakeDingTalk(union, {union: (f"{PREFIX}ding", "丁一")})

    first = await sso_service.login_with_auth_code(
        db, client, auth_code="code", state=sso_service.issue_state()
    )
    first_payload = auth_service.decode_access_token(first["token"])
    assert first_payload["session_id"]
    assert (await _authorize(db, first["token"])).id == first["user"].id

    second = await sso_service.login_with_auth_code(
        db, client, auth_code="code", state=sso_service.issue_state()
    )
    second_payload = auth_service.decode_access_token(second["token"])
    assert second_payload["session_id"] != first_payload["session_id"]

    with pytest.raises(SessionInvalidError):
        await _authorize(db, first["token"])
    assert (await _authorize(db, second["token"])).id == first["user"].id
