"""签名文件下载与存档的单元测试（change add-patent-fulltext-evidence，patent-risk-provider
「签名文件只从智慧芽文件域名下载」、patent-risk-check「附图与公报存档」）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.table.risk import evidence_store as es
from config import settings

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
NOW = datetime(2026, 11, 12, tzinfo=UTC)
URL = "https://data-pdf.zhihuiya.com/a.pdf?sign=x&response-expires=Sat%2C%2021%20Nov%202026%2006%3A39%3A17%20GMT"


class Bucket:
    def __init__(self):
        self.objects = {}

    def put_object(self, key, data, headers=None):
        self.objects[key] = (data, headers)


@pytest.fixture()
def oss(monkeypatch):
    b = Bucket()
    monkeypatch.setattr(es, "bucket", lambda: b)
    for k, v in (("OSS_ENABLED", True), ("OSS_ENDPOINT", "https://oss"), ("OSS_BUCKET", "b"),
                 ("OSS_ACCESS_KEY_ID", "id"), ("OSS_ACCESS_KEY_SECRET", "s")):
        monkeypatch.setattr(settings, k, v)
    return b


def _http(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False, trust_env=False)


async def _archive(handler, url=URL, kind="pdf"):
    async with _http(handler) as c:
        return await es.archive_file(c, url=url, kind=kind, check_id=1, candidate_id=2, seq=1, fetched_at=NOW)


@pytest.mark.parametrize("url,ok", [
    ("https://data-pdf.zhihuiya.com/a.pdf", True),
    ("https://data-fulltext-image.zhihuiya.com/a.png", True),
    ("http://data-pdf.zhihuiya.com/a.pdf", False),
    ("https://zhihuiya.com.evil.com/a.pdf", False),
    ("https://evilzhihuiya.com/a.pdf", False),
    ("https://user@data-pdf.zhihuiya.com/a.pdf", False),
    ("javascript:alert(1)", False),
    (None, False),
])
def test_trusted_url(url, ok):
    assert es.is_trusted_url(url) is ok


def test_link_expiry_from_signature_or_default():
    assert es.link_expires_at(URL, NOW) == datetime(2026, 11, 21, 6, 39, 17, tzinfo=UTC)
    assert es.link_expires_at("https://data-pdf.zhihuiya.com/a.pdf", NOW) == NOW + timedelta(days=7)


async def test_archive_pdf_success(oss):
    entry = await _archive(lambda req: httpx.Response(200, content=b"%PDF-1.7 body"))
    assert entry["archived"] is True and entry["content_type"] == "application/pdf"
    assert "link_url" not in entry
    key = entry["object_key"]
    assert key.startswith("risk-evidence/1/2/pdf-001-") and key.endswith(".pdf")
    assert oss.objects[key][1] == {"Content-Type": "application/pdf"}


async def test_untrusted_url_never_downloads(oss):
    calls = []
    entry = await _archive(lambda req: calls.append(req) or httpx.Response(200), url="https://evil.example.com/a.pdf")
    assert entry == {"seq": 1, "archived": False, "archive_error": es.UNTRUSTED_URL}
    assert calls == []


async def test_redirect_is_not_followed(oss):
    entry = await _archive(lambda req: httpx.Response(302, headers={"location": "https://evil.example.com/"}))
    assert entry["archived"] is False and entry["archive_error"] == es.DOWNLOAD_FAILED
    assert entry["link_url"] == URL


async def test_html_is_bad_content(oss):
    entry = await _archive(lambda req: httpx.Response(200, content=b"<html>login</html>"))
    assert entry["archive_error"] == es.BAD_CONTENT and oss.objects == {}


async def test_oversize_is_rejected(oss, monkeypatch):
    monkeypatch.setattr(es, "MAX_IMAGE_BYTES", 10)
    entry = await _archive(lambda req: httpx.Response(200, content=PNG),
                           url="https://data-fulltext-image.zhihuiya.com/a.png", kind="drawings")
    assert entry["archive_error"] == es.TOO_LARGE and oss.objects == {}


async def test_disabled_keeps_link(monkeypatch):
    monkeypatch.setattr(settings, "OSS_ENABLED", False)
    entry = await _archive(lambda req: pytest.fail("不应下载"))
    assert entry["archive_error"] == es.ARCHIVE_DISABLED and entry["link_url"] == URL


@pytest.mark.parametrize("content,expected", [
    (PNG, "image/png"), (b"\xff\xd8\xff\xe0", "image/jpeg"), (b"GIF89a", "image/gif"),
    (b"II*\x00", "image/tiff"), (b"RIFF\x00\x00\x00\x00WEBPVP8", "image/webp"), (b"%PDF-1.4", None),
])
def test_sniff_images(content, expected):
    got = es.sniff("drawings", content)
    assert (got[0] if got else None) == expected
