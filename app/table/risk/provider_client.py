"""智慧芽 Data REST API 客户端（change add-patent-risk-check 决策 1、3、4）。

契约见同目录 ``provider_contract.md``。三条纪律：

1. **源站固定。** ``BASE_URL`` 写死，不接受任何来自用户或配置的地址——Key 只能发往智慧芽。
2. **从不抛出供应商错误。** 每次调用都返回一个 :class:`CallResult`，由调用方落库留痕。网络、
   超时、5xx、HTML、结构异常一律归 ``unknown``（供应商可能已处理、已计费）；供应商明确
   返回的错误码归 ``error`` 并分类。供应商错误绝不映射为应用 401。
3. **不记 Key。** 日志只写接口编号、HTTP 状态和错误码；不记请求头、不记 ``error_msg``。

调用期间调用方不得持有数据库事务或连接（决策 3）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://connect.zhihuiya.com"

P010, P060, P011, P013, P042, P020 = "P010", "P060", "P011", "P013", "P042", "P020"
_PATHS = {
    P010: "/image-search/image-upload",
    P060: "/search/patent/image-single",
    P011: "/basic-patent-data/simple-bibliography",
    P013: "/basic-patent-data/legal-status",
    P042: "/basic-patent-data/fulltext-image",
    P020: "/basic-patent-data/pdf-data",
}
_GET_APIS = (P011, P013, P020)

#: P011/P013 单次批量上限（官方：逗号分隔，最多 100 个）。
MAX_BATCH_IDS = 100

# ---- 错误分类（决策 4）----
CREDENTIAL_INVALID = "credential_invalid"
NO_PERMISSION = "no_permission"
INSUFFICIENT_BALANCE = "insufficient_balance"
CLIENT_EXPIRED = "client_expired"
QUOTA_EXCEEDED = "quota_exceeded"
RATE_LIMITED = "rate_limited"
BAD_REQUEST = "bad_request"
UPSTREAM_ERROR = "upstream_error"
UNEXPECTED_CODE = "unexpected_code"
# 请求确定没有到达供应商（不会计费）：本地建客户端失败、连接阶段失败
CLIENT_ERROR = "client_error"
CONNECT_FAILED = "connect_failed"
# unknown 类
TIMEOUT = "timeout"
NETWORK = "network"
UPSTREAM_5XX = "upstream_5xx"
MALFORMED_RESPONSE = "malformed_response"

_CODE_CATEGORY: dict[int, str] = {
    67200003: CREDENTIAL_INVALID, 67200008: CREDENTIAL_INVALID, 67200009: CREDENTIAL_INVALID,
    67200004: NO_PERMISSION,
    67200005: INSUFFICIENT_BALANCE,
    67200006: CLIENT_EXPIRED,
    67200007: QUOTA_EXCEEDED,
    67200000: RATE_LIMITED, 67200001: RATE_LIMITED, 67200002: RATE_LIMITED,
    67200012: BAD_REQUEST, 68300004: BAD_REQUEST, 68300007: BAD_REQUEST, 68300010: BAD_REQUEST,
    67200100: UPSTREAM_ERROR, 67200101: UPSTREAM_ERROR, 68300005: UPSTREAM_ERROR,
    68300006: UPSTREAM_ERROR, 68300008: UPSTREAM_ERROR,
}
_HTTP_CATEGORY = {401: CREDENTIAL_INVALID, 403: NO_PERMISSION, 429: RATE_LIMITED}


def classify_error_code(code: int) -> str:
    return _CODE_CATEGORY.get(code, UNEXPECTED_CODE)


@dataclass
class CallResult:
    """一次 HTTP 调用的结果。``data`` 只在 ``outcome == 'success'`` 时可信。"""

    api: str
    outcome: str  # success / error / unknown
    started_at: datetime
    finished_at: datetime
    http_status: int | None = None
    error_code: int | None = None
    error_category: str | None = None
    data: Any = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.outcome == "success"


def _now() -> datetime:
    return datetime.now(UTC)


Validator = Callable[[Any], bool]


class ZhihuiyaClient:
    """无状态的薄客户端。``transport`` 只供测试注入（``httpx.MockTransport``）。"""

    def __init__(self, api_key: str, *, timeout: float = 30.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        if not api_key:
            raise ValueError("ZHIHUIYA_API_KEY 未配置")
        self._api_key = api_key
        self._timeout = timeout
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=self._timeout,
            transport=self._transport,
            headers={"Authorization": f"Bearer {self._api_key}"},
            follow_redirects=False,
            # 不读环境变量里的代理：源站固定，Key 不经环境配置的代理转发。本机 all_proxy=socks5
            # 曾让客户端在构造时就抛 ImportError（2026-11-10 联调）。
            trust_env=False,
        )

    async def _call(self, api: str, validate: Validator, **request: Any) -> CallResult:
        started = _now()
        method = "GET" if api in _GET_APIS else "POST"
        try:
            client = self._client()
        except Exception:
            # 请求没有发出，确定不会计费：记 error 而不是 unknown，也不能让任务卡在 running
            logger.exception("智慧芽 %s 客户端构造失败", api)
            return self._log(CallResult(api, "error", started, _now(), error_category=CLIENT_ERROR))
        try:
            async with client:
                resp = await client.request(method, _PATHS[api], **request)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.UnsupportedProtocol):
            # 连接没建立起来，请求没有到达供应商
            return self._log(CallResult(api, "error", started, _now(), error_category=CONNECT_FAILED))
        except httpx.TimeoutException:
            return self._log(CallResult(api, "unknown", started, _now(), error_category=TIMEOUT))
        except httpx.HTTPError:
            return self._log(CallResult(api, "unknown", started, _now(), error_category=NETWORK))

        finished = _now()
        status = resp.status_code
        try:
            body = resp.json()
        except ValueError:
            body = None

        if isinstance(body, dict) and (body.get("error_code") not in (None, 0) or body.get("status") is False):
            code = body.get("error_code")
            code = code if isinstance(code, int) else None
            category = classify_error_code(code) if code is not None else _HTTP_CATEGORY.get(status, UNEXPECTED_CODE)
            return self._log(CallResult(api, "error", started, finished, status, code, category))
        if status in _HTTP_CATEGORY:
            return self._log(CallResult(api, "error", started, finished, status, None, _HTTP_CATEGORY[status]))
        if status >= 500:
            return self._log(CallResult(api, "unknown", started, finished, status, None, UPSTREAM_5XX))
        if status != 200 or not isinstance(body, dict) or body.get("status") is not True:
            return self._log(CallResult(api, "unknown", started, finished, status, None, MALFORMED_RESPONSE))
        data = body.get("data")
        if not validate(data):
            return self._log(CallResult(api, "unknown", started, finished, status, 0, MALFORMED_RESPONSE))
        return self._log(CallResult(api, "success", started, finished, status, 0, None, data))

    @staticmethod
    def _log(result: CallResult) -> CallResult:
        # 只记接口编号、HTTP 状态、错误码与分类；不记请求头、URL 参数或供应商 error_msg。
        if result.ok:
            logger.info("智慧芽 %s 成功（HTTP %s）", result.api, result.http_status)
        else:
            logger.warning("智慧芽 %s %s：分类=%s HTTP=%s 错误码=%s", result.api, result.outcome,
                           result.error_category, result.http_status, result.error_code)
        return result

    # ---------------------------------------------------------------- 接口

    async def upload_image(self, content: bytes, content_type: str) -> CallResult:
        """P010：multipart 字段 ``image``。成功时 ``data`` = ``{"url", "expire"}``。"""
        ext = "png" if content_type == "image/png" else "jpg"
        return await self._call(
            P010, _valid_upload, files={"image": (f"image.{ext}", content, content_type)},
        )

    async def search_image(self, *, url: str, countries: list[str], limit: int, offset: int) -> CallResult:
        """P060：单图外观检索。``country`` 必须是数组，逗号字符串会被拒绝（2026-11-11 真实联调）。"""
        payload: dict[str, Any] = {
            "url": url, "patent_type": "D", "model": 1, "is_https": 1,
            "limit": limit, "offset": offset, "field": "SCORE",
        }
        if countries:
            payload["country"] = countries
        return await self._call(P060, lambda d: _valid_search(d, offset), json=payload)

    async def simple_bibliography(self, patent_ids: list[str]) -> CallResult:
        return await self._batch(P011, patent_ids)

    async def legal_status(self, patent_ids: list[str]) -> CallResult:
        return await self._batch(P013, patent_ids)

    async def fulltext_image(self, patent_id: str, *, limit: int, offset: int) -> CallResult:
        """P042：一件专利的全文附图，JSON 请求体。``data`` = ``{total_search_result_count, fulltext_image[]}``。"""
        return await self._call(P042, lambda d: _valid_fulltext(d, patent_id, offset),
                                json={"patent_id": patent_id, "limit": limit, "offset": offset})

    async def pdf_data(self, patent_id: str) -> CallResult:
        """P020：一件专利的公报 PDF。``data`` = ``[{patent_id, pn, pdf: {path}}]``，可能为空列表。"""
        return await self._call(P020, _valid_detail, params={"patent_id": patent_id})

    async def _batch(self, api: str, patent_ids: list[str]) -> CallResult:
        if not patent_ids or len(patent_ids) > MAX_BATCH_IDS:
            raise ValueError(f"patent_ids 数量须在 1..{MAX_BATCH_IDS}")
        return await self._call(api, _valid_detail, params={"patent_id": ",".join(patent_ids)})


# ---------------------------------------------------------------- 结构校验
# status=true 只代表供应商本次接口成功；字段不全时不能当成功（更不能当无命中）。


def _valid_upload(data: Any) -> bool:
    return isinstance(data, dict) and isinstance(data.get("url"), str) and data["url"].startswith("https://")


def _valid_search(data: Any, offset: int) -> bool:
    if not isinstance(data, dict):
        return False
    total = data.get("total_search_result_count")
    items = data.get("patent_messages")
    if not isinstance(total, int) or total < 0 or not isinstance(items, list):
        return False
    # 来源说还有结果，本页却是空的：结构异常，不是无命中
    if not items and total > offset:
        return False
    return all(isinstance(i, dict) and isinstance(i.get("patent_id"), str) and i["patent_id"] for i in items)


def _valid_fulltext(data: Any, patent_id: str, offset: int) -> bool:
    # 无附图时供应商可能返回 null 或空对象：按「无数据」处理，由调用方记为 missing
    if data is None or data == {}:
        return True
    if not isinstance(data, dict):
        return False
    total = data.get("total_search_result_count")
    items = data.get("fulltext_image")
    if not isinstance(total, int) or total < 0 or not isinstance(items, list):
        return False
    if data.get("patent_id") not in (None, patent_id):
        return False
    if not items and total > offset:
        return False
    return all(isinstance(i, dict) and isinstance(i.get("path"), str) for i in items)


def _valid_detail(data: Any) -> bool:
    return isinstance(data, list) and all(
        isinstance(i, dict) and isinstance(i.get("patent_id"), str) for i in data
    )
