"""用户域错误契约：CRUD 失败一律「抛」不「返」。

此前 `UserCenterCRUD` 的写方法返回 ``(success, message, user)`` 三元组，读方法则用
``except Exception: return []`` 把异常整个吞掉。两种写法各自有害：

- **返值式失败**要靠每个调用点自觉检查，漏检即静默。历史上「用户名已存在」被吞掉
  正是这个机制。API 层为此加了 ``_crud_failure()`` 按中文子串猜状态码
  （``"不存在" in message`` → 404），这是把结构化信息压成字符串又试图猜回来。
- **吞掉读异常**比丢栈更糟：全局处理器本会把 ``OperationalError`` 转成 503，
  吞掉之后数据库挂了却返回空列表——「查询失败」与「没有用户」在前端长得一模一样，
  且没有任何告警。

本文件守住重构后的契约。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from app.common.errors import (
    CODE_USER_DUPLICATE_NAME,
    CODE_USER_NOT_FOUND,
    APIError,
)
from app.user.user_crud import UserCenterCRUD
from app.user.user_errors import (
    DuplicateUserNameError,
    SuperUserDeactivationError,
    UserNotFoundError,
)
from app.user.user_schemas import UserCenterCreate, UserCenterUpdate


class _FakeSession:
    """够用的 Session 替身：只记录调用，不连库。

    ``execute_raises`` 设为异常实例时，``execute`` 抛它——用来模拟数据库故障。
    """

    def __init__(self, *, scalar_result=None, execute_raises=None, commit_raises=None):
        self.scalar_result = scalar_result
        self.execute_raises = execute_raises
        self.commit_raises = commit_raises
        self.committed = False
        self.rolled_back = False
        self.added = []

    async def execute(self, *_args, **_kwargs):
        if self.execute_raises is not None:
            raise self.execute_raises
        return SimpleNamespace(
            scalar_one_or_none=lambda: self.scalar_result,
            scalar=lambda: self.scalar_result,
            scalars=lambda: SimpleNamespace(all=lambda: self.scalar_result or []),
        )

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        if self.commit_raises is not None:
            raise self.commit_raises
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def refresh(self, _obj):
        pass


def _integrity_error(sqlstate: str) -> IntegrityError:
    """构造一个带 SQLSTATE 的完整性错误。

    真实驱动把错误码放在 ``orig`` 上：asyncpg 叫 ``sqlstate``，psycopg2 叫 ``pgcode``。
    这里用 asyncpg 的写法——用户表的写入走异步引擎。
    """
    orig = SimpleNamespace(sqlstate=sqlstate)
    return IntegrityError("INSERT ...", {}, orig)


def _unique_violation() -> IntegrityError:
    return _integrity_error("23505")


def _existing_user(**kwargs):
    base = dict(id=1, user_name="alice", is_active=True, is_super=False, hashed_pwd="x")
    base.update(kwargs)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------
# 写路径：失败抛出语义正确的异常，而不是返回元组
# --------------------------------------------------------------------------


class TestWritePathsRaise:
    async def test_create_user_duplicate_name_raises_409(self, monkeypatch):
        crud = UserCenterCRUD(_FakeSession())
        monkeypatch.setattr(crud, "get_user_by_username", _async_return(_existing_user()))

        with pytest.raises(DuplicateUserNameError) as exc:
            await crud.create_user(UserCenterCreate(user_name="alice", password="pwd12345"))

        assert exc.value.http_status == 409
        assert exc.value.code == CODE_USER_DUPLICATE_NAME

    async def test_create_user_unique_violation_raises_409(self, monkeypatch):
        """两个请求同时创建同名用户：先查后插挡不住，最后一道是库里的唯一约束。

        约束冲突若原样上浮，全局的 SQLAlchemyError 处理器会把它判成 500——
        「用户名已存在」是调用方能自己纠正的输入问题，不是服务端故障。
        """
        session = _FakeSession(commit_raises=_unique_violation())
        crud = UserCenterCRUD(session)
        monkeypatch.setattr(crud, "get_user_by_username", _async_return(None))
        monkeypatch.setattr(crud, "_default_role_id", _async_return(1))

        with pytest.raises(DuplicateUserNameError) as exc:
            await crud.create_user(UserCenterCreate(user_name="alice", password="pwd12345"))

        assert exc.value.http_status == 409
        assert exc.value.code == CODE_USER_DUPLICATE_NAME
        assert session.rolled_back

    async def test_create_user_other_integrity_error_still_propagates(self, monkeypatch):
        """只认唯一约束冲突。别的完整性错误（如外键）不能被冒充成「用户名已存在」。"""
        session = _FakeSession(commit_raises=_integrity_error("23503"))
        crud = UserCenterCRUD(session)
        monkeypatch.setattr(crud, "get_user_by_username", _async_return(None))
        monkeypatch.setattr(crud, "_default_role_id", _async_return(1))

        with pytest.raises(IntegrityError):
            await crud.create_user(UserCenterCreate(user_name="alice", password="pwd12345"))

    async def test_update_user_unique_violation_raises_409(self, monkeypatch):
        """改名撞上并发同名，同样是 409。"""
        session = _FakeSession(commit_raises=_unique_violation())
        crud = UserCenterCRUD(session)
        monkeypatch.setattr(crud, "get_user_by_id", _async_return(_existing_user()))
        monkeypatch.setattr(crud, "get_user_by_username", _async_return(None))

        with pytest.raises(DuplicateUserNameError):
            await crud.update_user(1, UserCenterUpdate(user_name="bob"))

        assert session.rolled_back

    async def test_authenticate_hashes_even_for_unknown_user(self, monkeypatch):
        """用户不存在时也要走一遍 bcrypt，否则响应时间就是用户名枚举接口。

        bcrypt 单次约 247 ms，跳过它的分支返回是毫秒级——两者差三个数量级，
        不用统计手段、肉眼看响应时间就能把有效用户名一个个试出来。
        """
        calls: list[tuple[str, str]] = []

        async def record(plain, hashed):
            calls.append((plain, hashed))
            return False

        crud = UserCenterCRUD(_FakeSession())
        monkeypatch.setattr(crud, "get_user_by_username", _async_return(None))
        monkeypatch.setattr(crud, "verify_password", record)

        assert await crud.authenticate_user("ghost", "pwd12345") is None
        assert len(calls) == 1, "用户不存在时跳过了密码校验，时间差可被观测"

    async def test_authenticate_hashes_even_for_inactive_user(self, monkeypatch):
        """被禁用的账号同理——「存在但禁用」也不该比「不存在」快。"""
        calls: list[tuple[str, str]] = []

        async def record(plain, hashed):
            calls.append((plain, hashed))
            return True

        crud = UserCenterCRUD(_FakeSession())
        monkeypatch.setattr(
            crud, "get_user_by_username", _async_return(_existing_user(is_active=False))
        )
        monkeypatch.setattr(crud, "verify_password", record)

        assert await crud.authenticate_user("alice", "pwd12345") is None
        assert len(calls) == 1

    async def test_dummy_hash_is_a_usable_bcrypt_hash(self):
        """哑哈希必须是真能被 verify 处理的串。

        写错一个字符不会有人发现：`verify_password` 会捕获异常返回 False，登录照样
        失败，只是每次未知用户名都往日志里写一条异常栈，且耗时回到毫秒级——
        时序防护静默失效。
        """
        from app.user import user_crud as mod

        assert mod.pwd_context.verify("not-the-password", mod._DUMMY_PASSWORD_HASH) is False

    async def test_update_user_missing_raises_404(self, monkeypatch):
        crud = UserCenterCRUD(_FakeSession())
        monkeypatch.setattr(crud, "get_user_by_id", _async_return(None))

        with pytest.raises(UserNotFoundError) as exc:
            await crud.update_user(99, UserCenterUpdate(is_active=False))

        assert exc.value.http_status == 404
        assert exc.value.code == CODE_USER_NOT_FOUND

    async def test_update_user_name_conflict_raises_409(self, monkeypatch):
        crud = UserCenterCRUD(_FakeSession())
        monkeypatch.setattr(crud, "get_user_by_id", _async_return(_existing_user()))
        monkeypatch.setattr(crud, "get_user_by_username", _async_return(_existing_user(id=2)))

        with pytest.raises(DuplicateUserNameError):
            await crud.update_user(1, UserCenterUpdate(user_name="bob"))

    async def test_toggle_missing_user_raises_404(self, monkeypatch):
        crud = UserCenterCRUD(_FakeSession())
        monkeypatch.setattr(crud, "get_user_by_id", _async_return(None))

        with pytest.raises(UserNotFoundError):
            await crud.toggle_user_status(99)

    async def test_disabling_superuser_raises_409(self, monkeypatch):
        """禁用超管是与当前状态冲突，不是参数错误，故 409 而非 400。"""
        crud = UserCenterCRUD(_FakeSession())
        monkeypatch.setattr(
            crud, "get_user_by_id", _async_return(_existing_user(is_super=True, is_active=True))
        )

        with pytest.raises(SuperUserDeactivationError) as exc:
            await crud.toggle_user_status(1)

        assert exc.value.http_status == 409

    async def test_write_failure_rolls_back_and_propagates(self, monkeypatch):
        """写失败必须回滚，且异常继续上抛交全局处理器——不吞、不转译成元组。"""
        boom = OperationalError("SELECT 1", {}, Exception("connection refused"))
        session = _FakeSession()
        crud = UserCenterCRUD(session)
        monkeypatch.setattr(crud, "get_user_by_username", _async_return(None))
        monkeypatch.setattr(crud, "_default_role_id", _async_return(1))
        monkeypatch.setattr(session, "commit", _async_raise(boom))

        with pytest.raises(OperationalError):
            await crud.create_user(UserCenterCreate(user_name="alice", password="pwd12345"))

        assert session.rolled_back is True


def _raise(exc):
    def _fn(*_a, **_kw):
        raise exc
    return _fn


def _async_raise(exc):
    """协程版的「一调用就抛」，用于替换 AsyncSession 的 commit 等协程方法。"""
    async def _fn(*_a, **_kw):
        raise exc
    return _fn


def _async_return(value):
    """协程版的固定返回值，用于替换已异步化的 CRUD 方法。"""
    async def _fn(*_a, **_kw):
        return value
    return _fn


# --------------------------------------------------------------------------
# 读路径：数据库异常必须上浮，不能被压成空结果
# --------------------------------------------------------------------------


class TestReadPathsDoNotSwallow:
    """这一组是本次重构最要紧的部分。

    全局处理器会把 OperationalError 转成 503「服务暂时不可用」并记栈；CRUD 若吞掉它
    返回空列表，前端看到的是「没有用户」，运维看到的是一片安静。
    """

    @pytest.mark.parametrize(
        "call",
        [
            lambda c: c.get_users_paginated(),
            lambda c: c.get_users_list(),
            lambda c: c.count_users(),
            lambda c: c.get_user_by_id(1),
            lambda c: c.get_user_by_username("alice"),
        ],
        ids=["paginated", "list", "count", "by_id", "by_username"],
    )
    async def test_database_failure_propagates(self, call):
        boom = OperationalError("SELECT 1", {}, Exception("connection refused"))
        crud = UserCenterCRUD(_FakeSession(execute_raises=boom))

        with pytest.raises(OperationalError):
            await call(crud)


# --------------------------------------------------------------------------
# API 层不再猜状态码
# --------------------------------------------------------------------------


async def test_api_layer_has_no_message_sniffing_translator():
    """``_crud_failure`` 按中文子串猜状态码，是元组签名的遗留补丁，应随之消失。

    它的存在意味着「用户不存在」这个事实先被压成字符串、再被 ``"不存在" in message``
    猜回来——改一个字的文案就会静默降级成 409。
    """
    import app.user.user_api as user_api

    assert not hasattr(user_api, "_crud_failure"), (
        "CRUD 已改为抛异常，API 层不应再有按 message 猜状态码的转译函数"
    )


async def test_user_errors_are_api_errors():
    """用户域异常必须是 APIError 子类，才会被全局处理器统一成错误契约。"""
    for cls in (DuplicateUserNameError, UserNotFoundError, SuperUserDeactivationError):
        assert issubclass(cls, APIError)
        assert cls.code and cls.message


# --------------------------------------------------------------------------
# HTTP 往返：用户真正看到的那份错误响应
# --------------------------------------------------------------------------


def _app_with_stubbed_crud(monkeypatch, method: str, raises: Exception):
    from fastapi import FastAPI

    import app.user.user_api as user_api
    from app.auth.simple_auth import simple_auth
    from app.common.error_handlers import register_error_handlers
    from database import get_db

    monkeypatch.setattr(user_api.UserCenterCRUD, method, _raise(raises))

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(user_api.user_router, prefix="/api/user")
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[simple_auth.get_current_user] = lambda: SimpleNamespace(
        id=1, user_name="admin", is_super=True, is_active=True
    )
    return app


@pytest.mark.parametrize(
    ("method", "exc", "path", "verb", "status", "code"),
    [
        ("create_user", DuplicateUserNameError(), "/api/user/create", "post", 409,
         CODE_USER_DUPLICATE_NAME),
        ("toggle_user_status", UserNotFoundError(), "/api/user/toggle-status/9", "post", 404,
         CODE_USER_NOT_FOUND),
        ("toggle_user_status", SuperUserDeactivationError(), "/api/user/toggle-status/1", "post",
         409, "user.super_protected"),
    ],
    ids=["duplicate-name-409", "missing-user-404", "super-protected-409"],
)
async def test_domain_errors_reach_client_with_correct_status_and_code(
    monkeypatch, method, exc, path, verb, status, code
):
    """状态码与错误码由异常类型决定，不再由中文文案猜出来。"""
    from fastapi.testclient import TestClient

    client = TestClient(_app_with_stubbed_crud(monkeypatch, method, exc))
    payload = {"user_name": "alice", "password": "pwd12345"} if verb == "post" else None
    r = getattr(client, verb)(path, json=payload)

    assert r.status_code == status
    body = r.json()
    assert body["success"] is False
    assert body["code"] == code
    assert "request_id" in body
