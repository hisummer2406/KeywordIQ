"""OpenAI 兼容的大模型客户端（change add-opportunity-ip-agent 决策 2）。

只做一件事：发一次 ``chat/completions``，要求 JSON 输出，返回解析后的对象。不做工具调用、
不做多轮循环——Agent 是两步受控生成（决策 1）。

- 基址、模型与 Key 只来自服务端 ``.env``；基址只接受 ``https://``，``trust_env=False``，不跟随重定向。
- 从不抛出供应商错误，返回 :class:`CallResult`（``api`` 字段放 ``llm``）。
- 日志不记 Key、提示词与回复正文。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.table.risk.provider_client import (
    BAD_REQUEST,
    CLIENT_ERROR,
    CONNECT_FAILED,
    CREDENTIAL_INVALID,
    INSUFFICIENT_BALANCE,
    MALFORMED_RESPONSE,
    NETWORK,
    NO_PERMISSION,
    RATE_LIMITED,
    TIMEOUT,
    UNEXPECTED_CODE,
    UPSTREAM_5XX,
    CallResult,
    _now,
)
from config import settings

logger = logging.getLogger(__name__)

API = "llm"
_HTTP = {400: BAD_REQUEST, 401: CREDENTIAL_INVALID, 402: INSUFFICIENT_BALANCE, 403: NO_PERMISSION,
         422: BAD_REQUEST, 429: RATE_LIMITED}


def llm_configured() -> bool:
    return bool(settings.OPPORTUNITY_LLM_API_KEY and settings.OPPORTUNITY_LLM_MODEL
                and settings.OPPORTUNITY_LLM_BASE_URL.startswith("https://"))


class LlmClient:
    def __init__(self, *, base_url: str, api_key: str, model: str, timeout: float = 120.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        if not (api_key and model and base_url.startswith("https://")):
            raise ValueError("大模型未配置或基址不是 https")
        self.model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._timeout = timeout
        self._transport = transport

    async def complete_json(self, system: str, user: str) -> CallResult:
        """成功时 ``data`` 是模型回复解析出的 JSON 对象（dict）。"""
        started = _now()
        try:
            client = httpx.AsyncClient(timeout=self._timeout, transport=self._transport,
                                       headers={"Authorization": f"Bearer {self._api_key}"},
                                       follow_redirects=False, trust_env=False)
        except Exception:
            logger.exception("大模型客户端构造失败")
            return self._log(CallResult(API, "error", started, _now(), error_category=CLIENT_ERROR))
        payload = {
            "model": self.model, "temperature": 0.2, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        try:
            async with client:
                resp = await client.post(self._url, json=payload)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.UnsupportedProtocol):
            return self._log(CallResult(API, "error", started, _now(), error_category=CONNECT_FAILED))
        except httpx.TimeoutException:
            return self._log(CallResult(API, "unknown", started, _now(), error_category=TIMEOUT))
        except httpx.HTTPError:
            return self._log(CallResult(API, "unknown", started, _now(), error_category=NETWORK))

        finished, status = _now(), resp.status_code
        if status in _HTTP:
            return self._log(CallResult(API, "error", started, finished, status, None, _HTTP[status]))
        if status >= 500:
            return self._log(CallResult(API, "unknown", started, finished, status, None, UPSTREAM_5XX))
        if status != 200:
            return self._log(CallResult(API, "error", started, finished, status, None, UNEXPECTED_CODE))
        data = _extract_json(resp)
        if data is None:
            return self._log(CallResult(API, "unknown", started, finished, status, None, MALFORMED_RESPONSE))
        result = CallResult(API, "success", started, finished, status, None, None, data)
        result.extra["usage"] = _usage(resp)
        return self._log(result)

    @staticmethod
    def _log(result: CallResult) -> CallResult:
        if result.ok:
            logger.info("大模型调用成功")
        else:
            logger.warning("大模型调用 %s：分类=%s HTTP=%s", result.outcome, result.error_category, result.http_status)
        return result


def _extract_json(resp: httpx.Response) -> dict[str, Any] | None:
    try:
        body = resp.json()
        content = body["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    if not isinstance(content, str):
        return None
    text = content.strip()
    # 个别模型即使要求 JSON 也会包一层 ```json 代码块
    if text.startswith("```"):
        text = text.strip("`")
        text = text[4:] if text.lower().startswith("json") else text
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _usage(resp: httpx.Response) -> dict[str, int]:
    try:
        u = resp.json().get("usage") or {}
    except ValueError:
        return {}
    return {k: v for k, v in u.items() if k in ("prompt_tokens", "completion_tokens", "total_tokens")
            and isinstance(v, int)}


def make_llm_client() -> LlmClient:
    """测试替换这个工厂注入 ``httpx.MockTransport``。"""
    return LlmClient(base_url=settings.OPPORTUNITY_LLM_BASE_URL, api_key=settings.OPPORTUNITY_LLM_API_KEY,
                     model=settings.OPPORTUNITY_LLM_MODEL, timeout=settings.OPPORTUNITY_LLM_TIMEOUT_SECONDS)
