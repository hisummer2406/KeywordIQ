"""智慧芽专利数据库 MCP 客户端（change add-opportunity-ip-agent 决策 3）。

契约见同目录 ``mcp_contract.md``。与 #24 的 REST 客户端同一套纪律：

1. **端点固定、工具白名单。** 只请求 ``ENDPOINT``，只允许 ``ALLOWED_TOOLS``。
2. **从不抛出供应商错误。** 每次 ``call`` 都返回 :class:`CallResult`（``api`` 字段放工具名），
   网络、超时、5xx、非 JSON、结构异常归 ``unknown``；供应商明确的错误码归 ``error``。
3. **不记 Key。** 日志只写工具名、HTTP 状态和错误码。

服务无会话（2026-11-11 实测 ``initialize`` 不返回 ``mcp-session-id``），同一个
:class:`McpSession` 内只初始化一次，然后连续 ``tools/call``。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx

from app.table.risk.provider_client import (
    _HTTP_CATEGORY,
    CLIENT_ERROR,
    CONNECT_FAILED,
    MALFORMED_RESPONSE,
    NETWORK,
    TIMEOUT,
    UNEXPECTED_CODE,
    UPSTREAM_5XX,
    UPSTREAM_ERROR,
    CallResult,
    _now,
    classify_error_code,
)

logger = logging.getLogger(__name__)

ENDPOINT = "https://connect.zhihuiya.com/1458a4/mcp"
PROTOCOL_VERSION = "2025-03-26"

SEARCH = "search_patents"
LEGAL = "get_patent_legal_status"
BIBLIO = "bibliography"
IMAGE = "abstract_image"
ALLOWED_TOOLS = (SEARCH, LEGAL, BIBLIO, IMAGE)
EMPTY_SEARCH: dict[str, Any] = {"result_count": 0, "total_search_result_count": 0, "results": []}

Validator = Callable[[Any], bool]


def _valid_search(offset: int) -> Validator:
    def check(data: Any) -> bool:
        if not isinstance(data, dict):
            return False
        total, items = data.get("total_search_result_count"), data.get("results")
        if not isinstance(total, int) or total < 0 or not isinstance(items, list):
            return False
        # 来源说还有结果，本次却是空的：结构异常，不是无命中
        if not items and total > offset:
            return False
        return all(isinstance(i, dict) and isinstance(i.get("patent_id"), str) and i["patent_id"] for i in items)
    return check


def _valid_detail(data: Any) -> bool:
    # 单件工具返回数组；查无此件时可能是空数组（由调用方记为 missing）
    return isinstance(data, list) and all(isinstance(i, dict) for i in data)


_VALIDATORS: dict[str, Callable[[dict[str, Any]], Validator]] = {
    SEARCH: lambda args: _valid_search(int(args.get("offset", 0))),
    LEGAL: lambda _: _valid_detail,
    BIBLIO: lambda _: _valid_detail,
    IMAGE: lambda _: _valid_detail,
}


def _parse_body(resp: httpx.Response) -> Any:
    """Streamable HTTP：``text/event-stream`` 取最后一条 ``data:``；否则按 JSON 解析。"""
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        payload = None
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
        if payload is None:
            raise ValueError("no data event")
        return json.loads(payload)
    return resp.json()


class McpSession:
    """一次任务执行内复用的 MCP 会话。用 ``async with`` 打开。"""

    def __init__(self, api_key: str, *, timeout: float = 30.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        if not api_key:
            raise ValueError("ZHIHUIYA_API_KEY 未配置")
        self._api_key = api_key
        self._timeout = timeout
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._initialized = False
        self._next_id = 1

    async def __aenter__(self) -> McpSession:
        try:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
                follow_redirects=False,
                # 不读环境变量里的代理：端点固定，Key 不经环境配置的代理转发（本机 all_proxy=socks5）
                trust_env=False,
            )
        except Exception:
            logger.exception("智慧芽 MCP 客户端构造失败")
            self._client = None
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        self._next_id += 1
        if params is not None:
            msg["params"] = params
        return msg

    async def _initialize(self) -> None:
        """初始化不计入工具调用；失败不在这里判定，交给随后的 tools/call 统一分类。"""
        if self._initialized or self._client is None:
            return
        try:
            await self._client.post(ENDPOINT, json=self._rpc("initialize", {
                "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                "clientInfo": {"name": "keywordiq", "version": "1"},
            }))
            await self._client.post(ENDPOINT, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
            self._initialized = True
        except httpx.HTTPError:
            pass

    async def call(self, tool: str, arguments: dict[str, Any]) -> CallResult:
        if tool not in ALLOWED_TOOLS:
            raise ValueError(f"工具不在白名单：{tool}")
        started = _now()
        if self._client is None:
            # 请求没有发出，确定不会计费
            return self._log(CallResult(tool, "error", started, _now(), error_category=CLIENT_ERROR))
        await self._initialize()
        try:
            resp = await self._client.post(
                ENDPOINT, json=self._rpc("tools/call", {"name": tool, "arguments": arguments}))
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.UnsupportedProtocol):
            return self._log(CallResult(tool, "error", started, _now(), error_category=CONNECT_FAILED))
        except httpx.TimeoutException:
            return self._log(CallResult(tool, "unknown", started, _now(), error_category=TIMEOUT))
        except httpx.HTTPError:
            return self._log(CallResult(tool, "unknown", started, _now(), error_category=NETWORK))
        return self._log(self._classify(tool, arguments, started, resp))

    def _classify(self, tool: str, arguments: dict[str, Any], started, resp: httpx.Response) -> CallResult:
        finished, status = _now(), resp.status_code
        if status in _HTTP_CATEGORY:
            return CallResult(tool, "error", started, finished, status, None, _HTTP_CATEGORY[status])
        if status >= 500:
            return CallResult(tool, "unknown", started, finished, status, None, UPSTREAM_5XX)
        try:
            body = _parse_body(resp)
        except ValueError:
            body = None
        if status != 200 or not isinstance(body, dict):
            return CallResult(tool, "unknown", started, finished, status, None, MALFORMED_RESPONSE)
        if isinstance(body.get("error"), dict):
            # JSON-RPC 层错误（如参数不合法）：供应商明确拒绝
            return CallResult(tool, "error", started, finished, status, None, UPSTREAM_ERROR)
        result = body.get("result")
        if not isinstance(result, dict):
            return CallResult(tool, "unknown", started, finished, status, None, MALFORMED_RESPONSE)
        sc = result.get("structuredContent")
        if not isinstance(sc, dict):
            sc = _content_json(result)
        if not isinstance(sc, dict):
            if result.get("isError") is True:
                return CallResult(tool, "error", started, finished, status, None, UPSTREAM_ERROR)
            return CallResult(tool, "unknown", started, finished, status, None, MALFORMED_RESPONSE)
        code = sc.get("error_code")
        code = code if isinstance(code, int) and not isinstance(code, bool) else None
        if sc.get("success") is not True or (code not in (None, 0)) or result.get("isError") is True:
            category = classify_error_code(code) if code else (UPSTREAM_ERROR if result.get("isError") else UNEXPECTED_CODE)
            return CallResult(tool, "error", started, finished, status, code, category)
        data = sc.get("data")
        if tool == SEARCH and data == {}:
            # 2026-11-11 实测：检索式合法但零命中时，供应商返回 success=true、data={}（不是空列表）。
            # 这是确定的「本次范围内未找到」，不是结构异常。
            data = dict(EMPTY_SEARCH)
        if not _VALIDATORS[tool](arguments)(data):
            return CallResult(tool, "unknown", started, finished, status, 0, MALFORMED_RESPONSE)
        return CallResult(tool, "success", started, finished, status, 0, None, data)

    @staticmethod
    def _log(result: CallResult) -> CallResult:
        if result.ok:
            logger.info("智慧芽 MCP %s 成功", result.api)
        else:
            logger.warning("智慧芽 MCP %s %s：分类=%s HTTP=%s 错误码=%s", result.api, result.outcome,
                           result.error_category, result.http_status, result.error_code)
        return result


def _content_json(result: dict[str, Any]) -> Any:
    """没有 structuredContent 时，退回解析第一段 text 内容（同一份 JSON）。"""
    for part in result.get("content") or []:
        if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
            try:
                return json.loads(part["text"])
            except ValueError:
                return None
    return None
