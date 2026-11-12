"""附图与公报的下载与 OSS 存档（change add-patent-fulltext-evidence 决策 2、4）。

P042、P020 返回的是供应商签名链接（附图约 7 天、PDF 约 10 天过期）。任务拿到链接后在同一任务里：

1. **只从智慧芽文件域名下载**：``https`` + 主机名以 ``.zhihuiya.com`` 结尾，不跟随重定向、不读环境代理、
   不带 API Key；流式读取并设上限；按文件头判断实际格式。
2. **写入 OSS 私有前缀**（阻塞的 oss2 走线程池）。
3. 任何一步失败都**不抛出**，只把条目降级为「仅链接」并记失败分类——接口调用已经成功（可能已计费），
   不能因为存档失败而让详情变成失败、诱导用户重复付费。

日志只记分类与主机名，不记完整签名链接。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from app.table.upload import oss_client
from config import settings

logger = logging.getLogger(__name__)

TRUSTED_HOST_SUFFIX = ".zhihuiya.com"
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_PDF_BYTES = 50 * 1024 * 1024
#: 签名链接没有 response-expires 时按 7 天估（2026-11-11 实测附图 7 天、PDF 10 天，取短的）。
DEFAULT_LINK_TTL = timedelta(days=7)
DOWNLOAD_TIMEOUT_SECONDS = 60.0

# 未存档分类
UNTRUSTED_URL = "untrusted_url"
DOWNLOAD_FAILED = "download_failed"
TOO_LARGE = "too_large"
BAD_CONTENT = "bad_content"
ARCHIVE_DISABLED = "archive_disabled"
ARCHIVE_FAILED = "archive_failed"

_IMAGE_MAGIC: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"GIF87a", "image/gif", "gif"),
    (b"GIF89a", "image/gif", "gif"),
    (b"II*\x00", "image/tiff", "tif"),
    (b"MM\x00*", "image/tiff", "tif"),
)


def _now() -> datetime:
    return datetime.now(UTC)


def is_trusted_url(url: Any) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and host.endswith(TRUSTED_HOST_SUFFIX) and not parts.username


def link_expires_at(url: str, fetched_at: datetime) -> datetime:
    """取签名链接里的 ``response-expires``（HTTP 日期）；没有或解析失败按取证时间 + 7 天。"""
    try:
        raw = parse_qs(urlsplit(url).query).get("response-expires", [None])[0]
        if raw:
            dt = parsedate_to_datetime(raw)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (TypeError, ValueError, IndexError):
        pass
    return fetched_at + DEFAULT_LINK_TTL


def sniff(kind: str, content: bytes) -> tuple[str, str] | None:
    """返回 ``(content_type, 扩展名)``；不符合该种类的格式返回 None。"""
    if kind == "pdf":
        return ("application/pdf", "pdf") if content.startswith(b"%PDF-") else None
    for magic, ctype, ext in _IMAGE_MAGIC:
        if content.startswith(magic):
            return ctype, ext
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None


def archive_enabled() -> bool:
    return bool(settings.OSS_ENABLED and settings.OSS_ENDPOINT and settings.OSS_BUCKET
                and settings.OSS_ACCESS_KEY_ID and settings.OSS_ACCESS_KEY_SECRET)


def object_key(check_id: int, candidate_id: int, kind: str, seq: int, sha256: str, ext: str) -> str:
    root = settings.RISK_EVIDENCE_OSS_PREFIX.strip("/")
    return f"{root}/{check_id}/{candidate_id}/{kind}-{seq:03d}-{sha256[:12]}.{ext}"


def bucket():
    """测试替换这个工厂注入桩 bucket。"""
    return oss_client._default_bucket()


def download_client() -> httpx.AsyncClient:
    """下载签名链接用的客户端。测试替换这个工厂注入 ``httpx.MockTransport``。"""
    return httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=False, trust_env=False)


async def _download(client: httpx.AsyncClient, url: str, limit: int) -> tuple[bytes | None, str | None]:
    try:
        async with client.stream("GET", url) as resp:
            if resp.status_code != 200:
                return None, DOWNLOAD_FAILED
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > limit:
                return None, TOO_LARGE
            buf = bytearray()
            async for chunk in resp.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > limit:
                    return None, TOO_LARGE
            return bytes(buf), None
    except httpx.HTTPError:
        return None, DOWNLOAD_FAILED


async def archive_file(client: httpx.AsyncClient, *, url: Any, kind: str, check_id: int, candidate_id: int,
                       seq: int, fetched_at: datetime) -> dict[str, Any]:
    """下载并存档一个签名文件，返回写进详情 ``files[]`` 的条目。从不抛出。"""
    entry: dict[str, Any] = {"seq": seq, "archived": False}
    if not is_trusted_url(url):
        entry["archive_error"] = UNTRUSTED_URL
        logger.warning("存档跳过：%s 第 %s 个文件的链接不在可信域名", kind, seq)
        return entry
    entry["link_url"] = url
    entry["link_expires_at"] = link_expires_at(url, fetched_at).isoformat()
    if not archive_enabled():
        entry["archive_error"] = ARCHIVE_DISABLED
        return entry

    content, err = await _download(client, url, MAX_PDF_BYTES if kind == "pdf" else MAX_IMAGE_BYTES)
    if content is None:
        entry["archive_error"] = err
        logger.warning("存档下载失败：%s 第 %s 个文件，分类=%s 主机=%s", kind, seq, err, urlsplit(url).hostname)
        return entry
    sniffed = sniff(kind, content)
    if sniffed is None:
        entry["archive_error"] = BAD_CONTENT
        logger.warning("存档跳过：%s 第 %s 个文件格式不符", kind, seq)
        return entry
    ctype, ext = sniffed
    sha = hashlib.sha256(content).hexdigest()
    key = object_key(check_id, candidate_id, kind, seq, sha, ext)
    try:
        b = bucket()
        await asyncio.to_thread(b.put_object, key, content, headers={"Content-Type": ctype})
    except Exception:
        logger.exception("存档写入 OSS 失败：%s 第 %s 个文件", kind, seq)
        entry["archive_error"] = ARCHIVE_FAILED
        return entry
    entry.update(archived=True, object_key=key, content_type=ctype, size_bytes=len(content),
                 sha256=sha, archived_at=_now().isoformat())
    entry.pop("link_url", None)
    entry.pop("link_expires_at", None)
    return entry


async def read_file(key: str) -> bytes:
    """从 OSS 读回存档文件（阻塞 SDK 走线程池）。"""
    def _read() -> bytes:
        return bucket().get_object(key).read()
    return await asyncio.to_thread(_read)
