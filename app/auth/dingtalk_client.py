"""钉钉开放平台客户端：企业内部应用的 OAuth2 登录与通讯录反查。

**这套不是 OIDC，尽管授权 URL 里的 scope 叫 openid。** 钉钉换回来的是 accessToken，
没有 id_token、没有 JWKS、没有 discovery 端点——接不上任何 OIDC 依赖方库，只能手写。

**两套凭证，别混淆：**

- **用户 accessToken**（`oauth2/userAccessToken`）：拿 authCode 换，只能读**个人**身份。
  `/v1.0/contact/users/me` 给的是 unionId、nick、头像，钉钉文档明写「无手机号和企业
  相关信息」。
- **企业 accessToken**（`oauth2/accessToken`）：拿 AppKey/AppSecret 换，用来调通讯录接口
  拿企业内 userid、姓名与在职状态。它有缓存与并发保护，见 :meth:`corp_access_token`。

新旧接口混用注意：换凭证与个人信息走新版 `api.dingtalk.com`（header 传 token），
通讯录反查走老版 `oapi.dingtalk.com` 的 topapi（query 传 token）。这不是历史包袱没清，
是钉钉现行文档就这么规定的。真正被废弃的是 `connect/oauth2/sns_authorize` +
`sns/getuserinfo_bycode` 那一套（2021-12 迁入历史文档区），本模块没有用到。
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from app.auth.sso_errors import (
    SsoCodeInvalidError,
    SsoProviderUnavailableError,
)

logger = logging.getLogger(__name__)

# 新版服务端 API（换凭证、个人信息）
API_BASE = "https://api.dingtalk.com"
# 老版 topapi（通讯录），钉钉现行文档指定
OAPI_BASE = "https://oapi.dingtalk.com"

# 扫码授权页。参数见 issue_authorize_url 的调用方（sso_service）。
AUTHORIZE_URL = "https://login.dingtalk.com/oauth2/auth"

#: 企业 accessToken 提前多久续期。钉钉给的有效期是 7200 秒，提前 300 秒换掉，
#: 避免"取出来时还有 2 秒有效"这种恰好过期的窗口。
_CORP_TOKEN_REFRESH_MARGIN = 300

#: 单次对钉钉的调用超时（秒）。登录是用户在等的同步路径，不能无限等。
_TIMEOUT = 10.0


class DingTalkClient:
    """对钉钉开放平台的调用。所有失败都翻译成 `sso_errors` 里的域内异常。

    `http_client` 可注入，测试用 `httpx.MockTransport` 顶掉真实网络。
    """

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        http_client: httpx.AsyncClient | None = None,
    ):
        self._app_key = app_key
        self._app_secret = app_secret
        self._http = http_client or httpx.AsyncClient(timeout=_TIMEOUT)
        # 企业 accessToken 的进程内缓存。每个 worker 各存一份：4 worker × 每 2 小时
        # 一次换取，可以忽略；落库只是给自己多一份需要保护的秘密。
        self._corp_token: str | None = None
        self._corp_token_expires_at: float = 0.0
        self._corp_token_lock = asyncio.Lock()

    # ---------- 用户身份（用户 accessToken 那套） ----------

    async def exchange_user_token(self, auth_code: str) -> str:
        """authCode → 用户 accessToken。

        authCode 一次性。刷新回调页、或后退再前进都会重放同一个 code，那是用户最容易
        撞上的失败，因此单独翻译成 `SsoCodeInvalidError`（文案指向"重新登录"）而不是
        笼统的"钉钉不可用"。
        """
        payload = {
            "clientId": self._app_key,
            "clientSecret": self._app_secret,
            "code": auth_code,
            "grantType": "authorization_code",
        }
        data = await self._post_json(f"{API_BASE}/v1.0/oauth2/userAccessToken", json=payload)
        token = data.get("accessToken")
        if not token:
            raise SsoProviderUnavailableError(
                log_detail=f"userAccessToken 响应缺少 accessToken: {data}"
            )
        return token

    async def contact_me(self, user_access_token: str) -> dict:
        """用户 accessToken → 个人身份（unionId、nick、avatarUrl）。

        注意这里拿不到企业相关信息，企业内 userid 要靠 :meth:`userid_by_unionid`。
        """
        return await self._get_json(
            f"{API_BASE}/v1.0/contact/users/me",
            headers={"x-acs-dingtalk-access-token": user_access_token},
        )

    # ---------- 企业凭证 ----------

    async def corp_access_token(self) -> str:
        """企业 accessToken，带缓存与并发保护。

        锁不是可选项：没有它，同时到达的登录会各自换取一次并互相覆盖缓存，钉钉侧还会
        因频繁换取而限流——而限流的表现是随机的登录失败，极难归因。

        双重检查（进锁前后各看一次缓存）让常态路径不必等锁。
        """
        if self._corp_token_valid():
            return self._corp_token  # type: ignore[return-value]

        async with self._corp_token_lock:
            # 等锁期间可能已经有人换好了
            if self._corp_token_valid():
                return self._corp_token  # type: ignore[return-value]

            data = await self._post_json(
                f"{API_BASE}/v1.0/oauth2/accessToken",
                json={"appKey": self._app_key, "appSecret": self._app_secret},
            )
            token = data.get("accessToken")
            expire_in = data.get("expireIn")
            if not token or not expire_in:
                raise SsoProviderUnavailableError(
                    log_detail=f"accessToken 响应缺少字段: {data}"
                )
            self._corp_token = token
            self._corp_token_expires_at = time.monotonic() + expire_in - _CORP_TOKEN_REFRESH_MARGIN
            return token

    def _corp_token_valid(self) -> bool:
        return bool(self._corp_token) and time.monotonic() < self._corp_token_expires_at

    # ---------- 通讯录（老版 topapi） ----------

    async def userid_by_unionid(self, corp_token: str, unionid: str) -> str | None:
        """unionId → 企业内 userid；不在通讯录内返回 ``None``。

        **离职同步就落在这一步**：钉钉侧把人移出通讯录后，这里拿不到 userid。

        钉钉在这种情况下返回的是 **HTTP 200 + errcode 非 0**，不是 4xx。把它当成异常
        处理，会让「这个人离职了」被误报成「钉钉挂了」——前者该拒登并提示联系管理员，
        后者该报 503 并提示改用密码登录，两种处置完全不同。
        """
        data = await self._post_json(
            f"{OAPI_BASE}/topapi/user/getbyunionid",
            params={"access_token": corp_token},
            json={"unionid": unionid},
        )
        if int(data.get("errcode", 0)) != 0:
            logger.info(
                "unionId 不在通讯录内: errcode=%s errmsg=%s",
                data.get("errcode"),
                data.get("errmsg"),
            )
            return None
        return (data.get("result") or {}).get("userid")

    async def user_detail(self, corp_token: str, userid: str) -> dict:
        """userid → 用户详情（姓名等）。取不到详情不算致命，返回空字典由调用方兜底。"""
        data = await self._post_json(
            f"{OAPI_BASE}/topapi/v2/user/get",
            params={"access_token": corp_token},
            json={"userid": userid},
        )
        if int(data.get("errcode", 0)) != 0:
            logger.info(
                "取用户详情失败: errcode=%s errmsg=%s", data.get("errcode"), data.get("errmsg")
            )
            return {}
        return data.get("result") or {}

    # ---------- 传输 ----------

    async def _post_json(self, url: str, **kwargs) -> dict:
        return await self._request("POST", url, **kwargs)

    async def _get_json(self, url: str, **kwargs) -> dict:
        return await self._request("GET", url, **kwargs)

    async def _request(self, method: str, url: str, **kwargs) -> dict:
        try:
            response = await self._http.request(method, url, timeout=_TIMEOUT, **kwargs)
        except httpx.HTTPError as exc:
            # 网络层失败一律 503：与数据库不可用同属基础设施类，需与业务 500 分开告警。
            raise SsoProviderUnavailableError(
                log_detail=f"{method} {url} 失败: {exc!r}"
            ) from exc

        if response.status_code >= 400:
            body = response.text[:500]
            # 授权码失效是用户最容易撞上的失败（刷新回调页即重放），单独翻译。
            if response.status_code == 400:
                raise SsoCodeInvalidError(log_detail=f"{method} {url} -> 400: {body}")
            raise SsoProviderUnavailableError(
                log_detail=f"{method} {url} -> {response.status_code}: {body}"
            )

        try:
            return response.json()
        except ValueError as exc:
            raise SsoProviderUnavailableError(
                log_detail=f"{method} {url} 返回了非 JSON: {response.text[:200]}"
            ) from exc
