"""钉钉单点登录的单元测试（不连库、不打真网）。

测试里对 SSO 相关配置一律**显式传参**而非依赖 .env——init 参数在 pydantic-settings 里
优先级最高，因此本机 .env 里写了什么都不会影响这些用例。这不是洁癖：issue #41 记着
本地 .env 覆盖 GROWTH_MIN_DAILY_RATE 让两条集成测试稳定失败（CI 却是绿的），
那种失败从报错信息里完全看不出成因。
"""
import unittest

from pydantic import ValidationError

from app.user.user_model import UserCenter
from config import Settings

# 配齐一组合法的 SSO 配置，各用例按需覆盖其中一项。
_VALID_SSO = {
    "SSO_ENABLED": True,
    "DINGTALK_APP_KEY": "dingtestappkey",
    "DINGTALK_APP_SECRET": "testsecret",
    "SSO_REDIRECT_URI": "https://example.com/sso/callback",
}


class TestSsoConfigValidation(unittest.TestCase):
    """`SSO_ENABLED=true` 时凭证必须齐全，否则启动即失败。

    配了却不生效的开关比没有更糟：症状是员工扫完码回来看到一个说不清的错误，
    而配置本身看起来是「已经开了」。
    """

    def test_enabled_without_app_secret_rejected(self):
        settings_kwargs = {**_VALID_SSO, "DINGTALK_APP_SECRET": ""}

        with self.assertRaises(ValidationError) as ctx:
            Settings(**settings_kwargs)

        self.assertIn("DINGTALK_APP_SECRET", str(ctx.exception))

    def test_enabled_without_app_key_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            Settings(**{**_VALID_SSO, "DINGTALK_APP_KEY": ""})

        self.assertIn("DINGTALK_APP_KEY", str(ctx.exception))

    def test_enabled_without_redirect_uri_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            Settings(**{**_VALID_SSO, "SSO_REDIRECT_URI": ""})

        self.assertIn("SSO_REDIRECT_URI", str(ctx.exception))

    def test_enabled_with_full_credentials_accepted(self):
        settings = Settings(**_VALID_SSO)

        self.assertTrue(settings.SSO_ENABLED)
        self.assertEqual(settings.DINGTALK_APP_KEY, "dingtestappkey")

    def test_disabled_does_not_require_credentials(self):
        """关闭时凭证留空必须合法——本地开发与 CI 都是这个状态。"""
        settings = Settings(
            SSO_ENABLED=False,
            DINGTALK_APP_KEY="",
            DINGTALK_APP_SECRET="",
            SSO_REDIRECT_URI="",
        )

        self.assertFalse(settings.SSO_ENABLED)

    def test_default_role_code_has_a_value(self):
        """默认角色不能为空——建档时没有角色可赋，SSO 账号会是个登不进任何页面的空壳。

        它是否**存在于角色表**这件事配置层判不了（要连库），由启动期检查负责。
        """
        settings = Settings(**_VALID_SSO)

        self.assertTrue(settings.SSO_DEFAULT_ROLE_CODE)


class TestUserCenterExternalIdentityColumns(unittest.TestCase):
    """`user_center` 承载外部身份所需的三列，以及可空的口令散列。"""

    @property
    def _columns(self):
        return UserCenter.__table__.columns

    def test_external_identity_columns_exist_and_are_nullable(self):
        """本地账号这两列为 NULL，所以必须可空。"""
        for name in ("external_idp", "external_id"):
            with self.subTest(column=name):
                self.assertIn(name, self._columns)
                self.assertTrue(self._columns[name].nullable)

    def test_display_name_column_exists(self):
        """显示名单独成列：不加的话界面上全是英文工号。"""
        self.assertIn("display_name", self._columns)
        self.assertTrue(self._columns["display_name"].nullable)

    def test_hashed_pwd_is_nullable(self):
        """SSO 账号没有密码，这是事实而非哨兵值。

        塞一个"永不匹配"的哨兵串，其正确性依赖「bcrypt 不会碰巧匹配」这条隐含约定；
        NULL 不需要任何约定。
        """
        self.assertTrue(self._columns["hashed_pwd"].nullable)

    def test_external_identity_pair_is_unique(self):
        """账号只按 (external_idp, external_id) 匹配，这一对必须唯一。

        没有这条约束，同一个钉钉身份可能对应多行，登录时命中哪一行取决于查询顺序。
        """
        pairs = {
            tuple(sorted(col.name for col in constraint.columns))
            for constraint in UserCenter.__table__.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }

        self.assertIn(("external_id", "external_idp"), pairs)


class TestSsoErrorContract(unittest.TestCase):
    """SSO 失败走统一错误契约：语义化 code + 按语义选的状态码 + 可直接展示的中文。"""

    def test_error_classes_carry_expected_status_and_code(self):
        from app.auth import sso_errors

        expected = [
            (sso_errors.SsoStateInvalidError, 400, "sso.state_invalid"),
            (sso_errors.SsoCodeInvalidError, 400, "sso.code_invalid"),
            (sso_errors.SsoNotEmployeeError, 403, "sso.not_employee"),
            (sso_errors.SsoAccountDisabledError, 403, "sso.account_disabled"),
            (sso_errors.SsoProviderUnavailableError, 503, "sso.provider_unavailable"),
        ]

        for cls, status, code in expected:
            with self.subTest(error=cls.__name__):
                exc = cls()
                self.assertEqual(exc.http_status, status)
                self.assertEqual(exc.code, code)
                self.assertTrue(exc.message)

    def test_provider_unavailable_is_503_not_500(self):
        """钉钉挂了与我们的代码炸了要能分别告警，与数据库不可用同属基础设施类。"""
        from app.auth.sso_errors import SsoProviderUnavailableError

        self.assertEqual(SsoProviderUnavailableError().http_status, 503)

    def test_log_detail_never_reaches_response_body(self):
        """钉钉的 errcode/errmsg、unionId、AppKey 只进日志。"""
        import asyncio
        import json

        from app.auth.sso_errors import SsoProviderUnavailableError
        from app.common.error_handlers import api_error_handler

        exc = SsoProviderUnavailableError(
            log_detail="dingtalk errcode=88 errmsg=ding_open_errcode unionId=abc123"
        )

        # 处理器要读 request.method 与 request.url.path 写日志；用真的 Request 构造，
        # 免得断言挂在一个自己捏的替身上。
        from starlette.requests import Request

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/auth/sso/callback",
                "headers": [],
                "query_string": b"",
            }
        )
        response = asyncio.run(api_error_handler(request, exc))
        body = json.loads(response.body)

        self.assertNotIn("unionId", json.dumps(body, ensure_ascii=False))
        self.assertNotIn("errcode", json.dumps(body, ensure_ascii=False))
        self.assertFalse(body["success"])
        self.assertEqual(body["code"], "sso.provider_unavailable")


class TestDingTalkClientCorpToken(unittest.TestCase):
    """企业 accessToken 的缓存与并发保护。

    它与用户 accessToken 是**两套凭证**：用户那套只拿得到个人身份（unionId、nick），
    企业内 userid、姓名与在职状态必须用企业这套去调通讯录接口。
    """

    def _client(self, handler):
        import httpx

        from app.auth.dingtalk_client import DingTalkClient

        transport = httpx.MockTransport(handler)
        return DingTalkClient(
            app_key="k",
            app_secret="s",
            http_client=httpx.AsyncClient(transport=transport),
        )

    def test_concurrent_callers_trigger_only_one_fetch(self):
        """并发登录只换取一次。

        没有锁的话每个并发登录各换一次，彼此覆盖——钉钉侧还会因为频繁换取而限流，
        而限流的表现是随机登录失败。
        """
        import asyncio

        import httpx

        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            return httpx.Response(200, json={"accessToken": "corp-token", "expireIn": 7200})

        client = self._client(handler)

        async def run():
            return await asyncio.gather(*[client.corp_access_token() for _ in range(5)])

        tokens = asyncio.run(run())

        self.assertEqual(tokens, ["corp-token"] * 5)
        self.assertEqual(len(calls), 1, f"应只换取一次，实际 {len(calls)} 次")

    def test_cached_token_is_reused_before_expiry(self):
        import asyncio

        import httpx

        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            return httpx.Response(200, json={"accessToken": "corp-token", "expireIn": 7200})

        client = self._client(handler)

        async def run():
            await client.corp_access_token()
            await client.corp_access_token()

        asyncio.run(run())

        self.assertEqual(len(calls), 1)

    def test_timeout_maps_to_provider_unavailable(self):
        """钉钉不可达 → 503，不是 500。"""
        import asyncio

        import httpx

        from app.auth.sso_errors import SsoProviderUnavailableError

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("connect timeout", request=request)

        client = self._client(handler)

        with self.assertRaises(SsoProviderUnavailableError):
            asyncio.run(client.corp_access_token())


class TestDingTalkClientIdentity(unittest.TestCase):
    """换取用户身份与通讯录反查。"""

    def _client(self, handler):
        import httpx

        from app.auth.dingtalk_client import DingTalkClient

        return DingTalkClient(
            app_key="k",
            app_secret="s",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    def test_expired_auth_code_maps_to_code_invalid(self):
        """authCode 一次性：刷新回调页就会重放同一个 code，这是最常见的失败。"""
        import asyncio

        import httpx

        from app.auth.sso_errors import SsoCodeInvalidError

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400, json={"code": "invalidParameter", "message": "code is expired"}
            )

        client = self._client(handler)

        with self.assertRaises(SsoCodeInvalidError):
            asyncio.run(client.exchange_user_token("used-code"))

    def test_userid_lookup_returns_none_when_not_in_directory(self):
        """不在通讯录内返回 None，由服务层翻译成拒登——离职同步就落在这一步。

        钉钉此处返回的是 HTTP 200 + errcode 非 0，**不是** 4xx；当成异常处理会把
        「这个人离职了」误报成「钉钉挂了」。
        """
        import asyncio

        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"errcode": 60121, "errmsg": "找不到该用户"}
            )

        client = self._client(handler)
        result = asyncio.run(client.userid_by_unionid("corp-token", "union-not-here"))

        self.assertIsNone(result)

    def test_userid_lookup_returns_userid_when_found(self):
        import asyncio

        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"errcode": 0, "errmsg": "ok", "result": {"userid": "zhangsan"}},
            )

        client = self._client(handler)
        result = asyncio.run(client.userid_by_unionid("corp-token", "union-1"))

        self.assertEqual(result, "zhangsan")


class TestSsoState(unittest.TestCase):
    """state 是无状态短命签名令牌，跨 worker 校验必须成立。

    `start.sh` 固定起 4 个 uvicorn worker，签发 state 与校验 state 落在同一进程纯属偶然。
    存进程内存的话失败率约 3/4，且**时好时坏**——症状与钉钉侧偶发故障无从区分。
    """

    def test_issued_state_verifies(self):
        from app.auth.sso_service import issue_state, verify_state

        state = issue_state()

        verify_state(state)  # 不抛即通过

    def test_state_is_not_stored_anywhere(self):
        """同一份签名令牌能被"另一个进程"校验——这里用全新导入的模块模拟。

        若实现改成往模块级 set 里记 nonce，这条会红。
        """
        from app.auth.sso_service import issue_state

        state = issue_state()
        # 模拟另一个 worker：清掉任何可能的模块级状态
        import importlib

        import app.auth.sso_service as mod

        importlib.reload(mod)

        mod.verify_state(state)

    def test_tampered_state_rejected(self):
        from app.auth.sso_errors import SsoStateInvalidError
        from app.auth.sso_service import issue_state, verify_state

        state = issue_state()

        with self.assertRaises(SsoStateInvalidError):
            verify_state(state[:-4] + "AAAA")

    def test_missing_state_rejected(self):
        from app.auth.sso_errors import SsoStateInvalidError
        from app.auth.sso_service import verify_state

        with self.assertRaises(SsoStateInvalidError):
            verify_state("")

    def test_expired_state_rejected(self):
        import datetime as dt

        import jwt

        from app.auth.sso_errors import SsoStateInvalidError
        from app.auth.sso_service import verify_state
        from config import settings

        stale = jwt.encode(
            {
                "nonce": "x",
                "exp": dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1),
                "typ": "sso_state",
            },
            settings.ADMIN_SECRET_KEY,
            algorithm="HS256",
        )

        with self.assertRaises(SsoStateInvalidError):
            verify_state(stale)

    def test_each_state_carries_a_fresh_nonce(self):
        import jwt

        from app.auth.sso_service import issue_state
        from config import settings

        payloads = [
            jwt.decode(issue_state(), settings.ADMIN_SECRET_KEY, algorithms=["HS256"])
            for _ in range(3)
        ]

        self.assertEqual(len({p["nonce"] for p in payloads}), 3)


class TestSsoUserNameNormalization(unittest.TestCase):
    """SSO 建档的用户名要遵守与管理员建号同一套规则。

    绕过它并不会报错（SSO 不走 UserCenterCreate 那个 Pydantic 模型），但库里会出现
    两套用户名规则——这种不一致没有任何地方会拦。
    """

    def test_normalizes_to_lowercase_alnum(self):
        from app.auth.sso_service import normalize_user_name

        self.assertEqual(normalize_user_name("Zhang-San_01"), "zhangsan01")

    def test_falls_back_when_nothing_survives_normalization(self):
        """纯中文或纯符号的 userid 剥完就空了，必须有兜底而不是建出空用户名。"""
        from app.auth.sso_service import normalize_user_name

        result = normalize_user_name("张三")

        self.assertTrue(result)
        self.assertTrue(result.isalnum())

    def test_collision_gets_numeric_suffix(self):
        from app.auth.sso_service import resolve_user_name

        taken = {"zhangsan", "zhangsan2"}

        self.assertEqual(resolve_user_name("zhangsan", taken), "zhangsan3")

    def test_no_collision_keeps_original(self):
        from app.auth.sso_service import resolve_user_name

        self.assertEqual(resolve_user_name("zhangsan", set()), "zhangsan")


class TestLocalPasswordLoginBoundary(unittest.TestCase):
    """本地密码登录保留，但只对本地账号有效。

    SSO 账号的 `hashed_pwd` 是 NULL，走 `POST /api/auth/token` 必须失败——**而且要
    走完整的哑哈希路径**。提前返回会让「这个用户名是 SSO 账号」通过响应耗时泄露出去，
    正是 `_DUMMY_PASSWORD_HASH` 当初要堵的那条侧信道。
    """

    def _crud_with_user(self, user):
        from app.user.user_crud import UserCenterCRUD

        crud = UserCenterCRUD(db=None)

        async def _get_user_by_username(_username):
            return user

        crud.get_user_by_username = _get_user_by_username
        return crud

    def _sso_user(self):
        from app.user.user_model import UserCenter

        return UserCenter(
            id=1,
            user_name="zhangsan",
            hashed_pwd=None,
            is_active=True,
            is_super=False,
            external_idp="dingtalk",
            external_id="union-1",
        )

    def test_sso_account_cannot_authenticate_with_password(self):
        import asyncio

        crud = self._crud_with_user(self._sso_user())

        result = asyncio.run(crud.authenticate_user("zhangsan", "anything"))

        self.assertIsNone(result)

    def test_sso_account_still_runs_a_real_hash_comparison(self):
        """断言真的拿一个**非 None 的散列**跑了校验，而不是提前返回。

        会让这条红的生产改动：在 authenticate_user 里加一句
        `if user.hashed_pwd is None: return None`。
        """
        import asyncio

        crud = self._crud_with_user(self._sso_user())
        seen = []

        async def _verify(plain, hashed):
            seen.append(hashed)
            return False

        crud.verify_password = _verify

        asyncio.run(crud.authenticate_user("zhangsan", "anything"))

        self.assertEqual(len(seen), 1, "必须跑一次密码校验，不能提前返回")
        self.assertIsNotNone(seen[0], "拿 None 去校验会快速失败，等于重开时序侧信道")

    def test_local_account_with_correct_password_still_works(self):
        """逃生通道必须还在：钉钉挂了时本地账号照常登录。"""
        import asyncio

        from app.user.user_model import UserCenter

        local = UserCenter(
            id=2, user_name="admin", hashed_pwd="$2b$12$whatever",
            is_active=True, is_super=True,
        )
        crud = self._crud_with_user(local)

        async def _verify(plain, hashed):
            return True

        crud.verify_password = _verify

        result = asyncio.run(crud.authenticate_user("admin", "pwd123"))

        self.assertIs(result, local)


if __name__ == "__main__":
    unittest.main()


class TestSsoEndpoints(unittest.TestCase):
    """三个免鉴权端点：status / authorize-url / callback。"""

    def _client(self, dingtalk=None):
        """建一个只挂 auth 路由的 app。

        钉钉客户端与数据库会话都用依赖覆盖顶掉：前者默认会去建真的 httpx.AsyncClient
        （在依赖解析期就发生，会读本机代理配置），后者要连库。这两样都与本组断言无关。
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.auth.auth_api import auth_router, get_dingtalk_client
        from app.common.error_handlers import register_error_handlers
        from database import get_async_db

        app = FastAPI()
        register_error_handlers(app)
        app.include_router(auth_router, prefix="/api/auth")
        app.dependency_overrides[get_dingtalk_client] = lambda: dingtalk
        app.dependency_overrides[get_async_db] = lambda: None
        return TestClient(app)

    def test_status_reports_disabled_without_credentials(self):
        """关闭时前端据此隐藏钉钉按钮。

        不这么做的话前端只能靠「请求授权地址拿到失败响应」来推断，每次进登录页都要
        发一个注定失败的请求。
        """
        from unittest.mock import patch

        with patch("config.settings.SSO_ENABLED", False):
            body = self._client().get("/api/auth/sso/status").json()

        self.assertFalse(body["enabled"])

    def test_status_is_open_without_a_token(self):
        """登录页还没有令牌，这个端点必须免鉴权，否则按钮永远出不来。"""
        response = self._client().get("/api/auth/sso/status")

        self.assertEqual(response.status_code, 200)

    def test_authorize_url_carries_required_params(self):
        from unittest.mock import patch
        from urllib.parse import parse_qs, urlparse

        with patch("config.settings.SSO_ENABLED", True), \
             patch("config.settings.DINGTALK_APP_KEY", "test-key"), \
             patch("config.settings.SSO_REDIRECT_URI", "https://app.example.com/sso/callback"):
            body = self._client().get("/api/auth/sso/authorize-url").json()

        parsed = urlparse(body["url"])
        params = parse_qs(parsed.query)

        self.assertEqual(parsed.netloc, "login.dingtalk.com")
        self.assertEqual(params["client_id"], ["test-key"])
        self.assertEqual(params["response_type"], ["code"])
        self.assertEqual(params["scope"], ["openid"])
        self.assertEqual(params["prompt"], ["consent"])
        self.assertTrue(params["state"][0])

    def test_redirect_uri_comes_from_config_not_the_request(self):
        """可由请求方传入即是开放重定向：攻击者能把授权码引到自己的域名上。"""
        from unittest.mock import patch
        from urllib.parse import parse_qs, urlparse

        with patch("config.settings.SSO_ENABLED", True), \
             patch("config.settings.DINGTALK_APP_KEY", "test-key"), \
             patch("config.settings.SSO_REDIRECT_URI", "https://app.example.com/sso/callback"):
            body = self._client().get(
                "/api/auth/sso/authorize-url",
                params={"redirect_uri": "https://evil.example.com/steal"},
            ).json()

        params = parse_qs(urlparse(body["url"]).query)

        self.assertEqual(params["redirect_uri"], ["https://app.example.com/sso/callback"])

    def test_authorize_url_rejected_when_sso_disabled(self):
        """关闭时不是"碰巧没有这条路由"，而是明确答复本部署没开 SSO。"""
        from unittest.mock import patch

        with patch("config.settings.SSO_ENABLED", False):
            response = self._client().get("/api/auth/sso/authorize-url")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "sso.disabled")

    def test_callback_rejects_bad_state_before_calling_dingtalk(self):
        """state 不合法就该止步，**一次都不去问钉钉**。

        拿来路不明的授权码去调用外部服务，等于把本系统变成一个可被驱动的代理。
        """
        from unittest.mock import patch

        called = []

        class SpyClient:
            async def exchange_user_token(self, auth_code):
                called.append(auth_code)
                return "should-not-happen"

        with patch("config.settings.SSO_ENABLED", True):
            response = self._client(dingtalk=SpyClient()).post(
                "/api/auth/sso/callback",
                json={"auth_code": "whatever", "state": "forged"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "sso.state_invalid")
        self.assertEqual(called, [], "state 没过就不该调用钉钉")


class TestSsoStartupRoleCheck(unittest.TestCase):
    """`SSO_DEFAULT_ROLE_CODE` 必须真的存在于角色表。

    配置层判不了这件事（要连库），所以放在启动期。角色不存在时建出来的账号没有任何
    权限——进每一个页面都 403，而管理员看到的是「这人明明登录成功了」。
    """

    class _FakeDb:
        def __init__(self, result):
            self._result = result

        async def scalar(self, _stmt):
            return self._result

    def test_missing_role_aborts_startup(self):
        import asyncio

        from app.auth.sso_service import assert_default_role_exists

        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(assert_default_role_exists(self._FakeDb(None)))

        self.assertIn("SSO_DEFAULT_ROLE_CODE", str(ctx.exception))

    def test_existing_role_passes(self):
        import asyncio

        from app.auth.sso_service import assert_default_role_exists

        asyncio.run(assert_default_role_exists(self._FakeDb(3)))
