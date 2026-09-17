"""飞书开放平台客户端：企业自建应用的 tenant_access_token 换取与消息/卡片发送。

`tenant_access_token` 的缓存与并发保护同构 `app/auth/dingtalk_client.py` 的企业
accessToken 那段——没有锁，并发到达的请求会各自换取一次并互相覆盖缓存，飞书侧还会
因频繁换取而限流，表现是随机的发送失败，极难归因。
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx

API_BASE = "https://open.feishu.cn/open-apis"

#: tenant_access_token 飞书给的有效期通常是 7200 秒，提前多久换掉。
_TOKEN_REFRESH_MARGIN = 300
_TIMEOUT = 10.0


class FeishuAPIError(Exception):
    """飞书开放平台调用失败（网络错误或返回非 0 code）。"""


class FeishuClient:
    """对飞书开放平台的调用。`http_client` 可注入，测试用 `httpx.MockTransport` 顶掉真实网络。"""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        http_client: httpx.AsyncClient | None = None,
    ):
        self._app_id = app_id
        self._app_secret = app_secret
        self._http = http_client or httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False)
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()

    async def tenant_access_token(self) -> str:
        """带缓存与并发保护的 tenant_access_token。双重检查让常态路径不必等锁。"""
        if self._token_valid():
            return self._token  # type: ignore[return-value]

        async with self._token_lock:
            if self._token_valid():
                return self._token  # type: ignore[return-value]

            data = await self._post_json(
                f"{API_BASE}/auth/v3/tenant_access_token/internal",
                json={"app_id": self._app_id, "app_secret": self._app_secret},
            )
            token = data.get("tenant_access_token")
            expire_in = data.get("expire")
            if not token or not expire_in:
                raise FeishuAPIError(f"tenant_access_token 响应缺少字段: {data}")
            self._token = token
            self._token_expires_at = time.monotonic() + expire_in - _TOKEN_REFRESH_MARGIN
            return token

    def _token_valid(self) -> bool:
        return bool(self._token) and time.monotonic() < self._token_expires_at

    async def send_text(self, chat_id: str, text: str) -> None:
        """向指定会话发一条文本消息。"""
        token = await self.tenant_access_token()
        await self._post_json(
            f"{API_BASE}/im/v1/messages?receive_id_type=chat_id",
            headers={"Authorization": f"Bearer {token}"},
            json={"receive_id": chat_id, "msg_type": "text", "content": json.dumps({"text": text})},
        )

    async def send_card(self, chat_id: str, card: dict) -> str:
        """向指定会话发一张互动卡片，返回消息 id（供后续 `update_card` 原地刷新用）。"""
        token = await self.tenant_access_token()
        data = await self._post_json(
            f"{API_BASE}/im/v1/messages?receive_id_type=chat_id",
            headers={"Authorization": f"Bearer {token}"},
            json={"receive_id": chat_id, "msg_type": "interactive", "content": json.dumps(card)},
        )
        message_id = (data.get("data") or {}).get("message_id")
        if not message_id:
            raise FeishuAPIError(f"发送卡片响应缺少 message_id: {data}")
        return message_id

    async def update_card(self, message_id: str, card: dict) -> None:
        """原地更新已发送的卡片（用于"受理→执行中→完成"的状态刷新，避免刷屏）。"""
        token = await self.tenant_access_token()
        await self._patch_json(
            f"{API_BASE}/im/v1/messages/{message_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"content": json.dumps(card), "msg_type": "interactive"},
        )

    # ---------- 传输 ----------

    async def _post_json(self, url: str, **kwargs) -> dict:
        return await self._request("POST", url, **kwargs)

    async def _patch_json(self, url: str, **kwargs) -> dict:
        return await self._request("PATCH", url, **kwargs)

    async def _request(self, method: str, url: str, **kwargs) -> dict:
        try:
            response = await self._http.request(method, url, timeout=_TIMEOUT, **kwargs)
        except httpx.HTTPError as exc:
            raise FeishuAPIError(f"{method} {url} 失败: {exc!r}") from exc

        if response.status_code >= 400:
            raise FeishuAPIError(f"{method} {url} 返回 {response.status_code}: {response.text}")

        data = response.json()
        code = data.get("code")
        if code not in (None, 0):
            raise FeishuAPIError(f"{method} {url} 返回业务错误 code={code}: {data.get('msg')}")
        return data
