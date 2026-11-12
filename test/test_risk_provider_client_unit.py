"""智慧芽客户端单元测试（change add-patent-risk-check，patent-risk-provider 规格）。

用 ``httpx.MockTransport`` 接住**真实客户端**发出的请求——核对的是实际的请求头、multipart、
JSON 与查询参数，而不是 mock 掉客户端方法本身。
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from app.table.risk import provider_client as pc
from app.table.risk.provider_client import ZhihuiyaClient

KEY = "sk-test-0000000000000000"


def _client(handler) -> ZhihuiyaClient:
    return ZhihuiyaClient(KEY, timeout=5, transport=httpx.MockTransport(handler))


def _ok(data):
    return httpx.Response(200, json={"status": True, "error_code": 0, "data": data})


def _item(pid="p1", **kw):
    return {"patent_id": pid, "patent_pn": "US-D1", "title": "Cup", "url": "https://img/x.png", **kw}


async def test_requires_key():
    with pytest.raises(ValueError):
        ZhihuiyaClient("")


async def test_upload_sends_bearer_and_multipart_image():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        seen["ctype"] = req.headers.get("content-type", "")
        seen["body"] = req.content
        return _ok({"url": "https://static/tmp.png", "expire": 32400})

    res = await _client(handler).upload_image(b"\x89PNG\r\n\x1a\nxx", "image/png")
    assert res.ok and res.data["url"] == "https://static/tmp.png"
    assert seen["url"] == "https://connect.zhihuiya.com/image-search/image-upload"
    assert seen["auth"] == f"Bearer {KEY}"
    assert seen["ctype"].startswith("multipart/form-data")
    assert b'name="image"' in seen["body"]
    assert "apikey" not in seen["url"]


async def test_search_sends_design_params():
    seen = {}

    def handler(req: httpx.Request):
        seen["path"] = req.url.path
        seen["json"] = json.loads(req.content)
        return _ok({"total_search_result_count": 1, "patent_messages": [_item()]})

    res = await _client(handler).search_image(url="https://static/tmp.png", countries=["US", "EU"],
                                              limit=20, offset=0)
    assert res.ok
    assert seen["path"] == "/search/patent/image-single"
    body = seen["json"]
    assert body["patent_type"] == "D" and body["model"] == 1 and body["is_https"] == 1
    assert body["country"] == ["US", "EU"] and body["limit"] == 20 and body["offset"] == 0


async def test_detail_batch_comma_ids():
    seen = {}

    def handler(req: httpx.Request):
        seen[req.url.path] = req.url.params.get("patent_id")
        return _ok([{"patent_id": "a"}, {"patent_id": "b"}])

    c = _client(handler)
    assert (await c.simple_bibliography(["a", "b"])).ok
    assert (await c.legal_status(["a", "b"])).ok
    assert seen == {"/basic-patent-data/simple-bibliography": "a,b", "/basic-patent-data/legal-status": "a,b"}
    with pytest.raises(ValueError):
        await c.legal_status([str(i) for i in range(101)])


async def test_no_hit_is_success():
    res = await _client(lambda r: _ok({"total_search_result_count": 0, "patent_messages": []})).search_image(
        url="https://x", countries=[], limit=10, offset=0)
    assert res.ok and res.data["patent_messages"] == []


@pytest.mark.parametrize(
    ("code", "category"),
    [
        (67200003, pc.CREDENTIAL_INVALID),
        (67200004, pc.NO_PERMISSION),
        (67200005, pc.INSUFFICIENT_BALANCE),
        (67200006, pc.CLIENT_EXPIRED),
        (67200007, pc.QUOTA_EXCEEDED),
        (67200002, pc.RATE_LIMITED),
        (68300010, pc.BAD_REQUEST),
        (68300008, pc.UPSTREAM_ERROR),
        (99999999, pc.UNEXPECTED_CODE),
    ],
)
async def test_vendor_error_codes_classified(code, category):
    res = await _client(lambda r: httpx.Response(
        200, json={"status": False, "error_code": code, "error_msg": "secret detail"})).legal_status(["a"])
    assert res.outcome == "error" and res.error_category == category and res.error_code == code


async def test_http_429_and_401_are_errors_not_app_auth():
    r429 = await _client(lambda r: httpx.Response(429, text="slow down")).legal_status(["a"])
    assert (r429.outcome, r429.error_category) == ("error", pc.RATE_LIMITED)
    r401 = await _client(lambda r: httpx.Response(401, text="")).legal_status(["a"])
    assert (r401.outcome, r401.error_category) == ("error", pc.CREDENTIAL_INVALID)


async def test_timeout_network_5xx_html_are_unknown():
    def timeout(req):
        raise httpx.ReadTimeout("t", request=req)

    def reset(req):
        raise httpx.ReadError("r", request=req)

    cases = [
        (timeout, pc.TIMEOUT),
        (reset, pc.NETWORK),
        (lambda r: httpx.Response(502, text="<html>bad gateway</html>"), pc.UPSTREAM_5XX),
        (lambda r: httpx.Response(200, text="<html>login</html>"), pc.MALFORMED_RESPONSE),
    ]
    for handler, category in cases:
        res = await _client(handler).search_image(url="https://x", countries=[], limit=10, offset=0)
        assert (res.outcome, res.error_category) == ("unknown", category)


async def test_unsent_requests_are_errors_not_unknown(monkeypatch):
    def refused(req):
        raise httpx.ConnectError("c", request=req)

    res = await _client(refused).legal_status(["a"])
    assert (res.outcome, res.error_category) == ("error", pc.CONNECT_FAILED)

    def broken(self):
        raise ImportError("socksio missing")

    monkeypatch.setattr(ZhihuiyaClient, "_client", broken)
    res = await _client(lambda r: _ok([])).legal_status(["a"])
    assert (res.outcome, res.error_category) == ("error", pc.CLIENT_ERROR)


async def test_ignores_environment_proxies(monkeypatch):
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    client = ZhihuiyaClient(KEY)._client()
    assert client._trust_env is False
    await client.aclose()


@pytest.mark.parametrize(
    "data",
    [
        {"total_search_result_count": 30, "patent_messages": []},  # 来源说有、列表却空
        {"total_search_result_count": 1, "patent_messages": [{"title": "no id"}]},  # 缺 patent_id
        {"patent_messages": []},  # 缺总数
        None,
    ],
)
async def test_malformed_success_is_unknown_not_no_hit(data):
    res = await _client(lambda r: _ok(data)).search_image(url="https://x", countries=[], limit=10, offset=0)
    assert (res.outcome, res.error_category) == ("unknown", pc.MALFORMED_RESPONSE)


async def test_empty_page_beyond_total_is_valid():
    res = await _client(lambda r: _ok({"total_search_result_count": 20, "patent_messages": []})).search_image(
        url="https://x", countries=[], limit=10, offset=20)
    assert res.ok


async def test_logs_never_contain_key_or_vendor_message(caplog):
    caplog.set_level(logging.DEBUG)
    await _client(lambda r: httpx.Response(
        200, json={"status": False, "error_code": 67200003, "error_msg": "vendor-secret-msg"})).legal_status(["a"])
    await _client(lambda r: _ok([{"patent_id": "a"}])).legal_status(["a"])
    text = caplog.text
    assert KEY not in text and "vendor-secret-msg" not in text and "Bearer" not in text


# ---------------------------------------------------------------- P042 / P020 与签名文件下载
# change add-patent-fulltext-evidence


async def test_fulltext_image_posts_json_and_validates():
    seen = {}

    def handler(req: httpx.Request):
        seen["method"], seen["path"] = req.method, req.url.path
        seen["body"] = json.loads(req.content)
        return _ok({"patent_id": "p1", "pn": "USD1", "total_search_result_count": 8, "result_count": 1,
                    "fulltext_image": [{"image_type": "drawing", "path": "https://data-fulltext-image.zhihuiya.com/a.png"}]})

    res = await _client(handler).fulltext_image("p1", limit=20, offset=0)
    assert res.ok and res.api == pc.P042
    assert seen == {"method": "POST", "path": "/basic-patent-data/fulltext-image",
                    "body": {"patent_id": "p1", "limit": 20, "offset": 0}}


@pytest.mark.parametrize("data", [
    {"patent_id": "other", "total_search_result_count": 1, "fulltext_image": [{"path": "https://x"}]},
    {"patent_id": "p1", "total_search_result_count": 5, "fulltext_image": []},
    {"patent_id": "p1", "total_search_result_count": 1, "fulltext_image": [{"image_type": "drawing"}]},
    ["not", "a", "dict"],
])
async def test_fulltext_image_malformed_is_unknown(data):
    res = await _client(lambda req: _ok(data)).fulltext_image("p1", limit=20, offset=0)
    assert res.outcome == "unknown" and res.error_category == pc.MALFORMED_RESPONSE


async def test_fulltext_image_null_data_is_success_without_items():
    res = await _client(lambda req: _ok(None)).fulltext_image("p1", limit=20, offset=0)
    assert res.ok and res.data is None


async def test_pdf_data_gets_single_patent():
    seen = {}

    def handler(req: httpx.Request):
        seen["method"], seen["path"], seen["pid"] = req.method, req.url.path, req.url.params["patent_id"]
        return _ok([{"patent_id": "p1", "pn": "USD1", "pdf": {"path": "https://data-pdf.zhihuiya.com/a.pdf"}}])

    res = await _client(handler).pdf_data("p1")
    assert res.ok and res.api == pc.P020
    assert seen == {"method": "GET", "path": "/basic-patent-data/pdf-data", "pid": "p1"}


async def test_file_error_code_is_classified():
    res = await _client(lambda req: httpx.Response(200, json={
        "status": False, "error_code": 67200004, "error_msg": "x"})).pdf_data("p1")
    assert res.outcome == "error" and res.error_category == pc.NO_PERMISSION
