"""竞对卖家标识解析：纯函数，不连库、不做任何 I/O。

书签导入和手动添加共用这一份解析逻辑，因为用户从浏览器复制来的一定是带 ``ds=`` 会话
签名的长 URL，两条入口必须同样容错。见
``openspec/changes/add-competitor-storefront-watch/design.md`` 决策 3。

**六种输入形态**：``i=merchant-items&me=`` 链接、``/sp?seller=`` 链接、
``/gp/help/seller/at-a-glance.html?seller=`` 链接、裸卖家 ID、以及两种明确拒绝的形态
（品牌店铺页 ``/stores/...``、商品详情页 ``/dp/...``）——这两种不静默丢弃，返回原文
与拒绝原因。

**站点识别**优先取 ``marketplaceID`` 查询参数，缺失时回落域名 TLD；两者都拿不到时，
如果调用方提供了 ``fallback_site``（预览/添加卖家接口上用户显式选定的站点），
用它兜底——**裸卖家 ID 天生不带站点信息，永远需要这个兜底**，没有提供时直接拒绝，
不猜测默认站点（与 ``site_registry.normalize_site_code`` 的既定纪律一致：非法/缺失
站点报错不回退）。

原始 URL 里的会话签名参数（``ds=`` 等）在这里直接被丢弃——从不读取、不存储、不用于
重建请求，只提取 ``seller_id``/``site`` 两个值。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

from app.site_registry import SITE_CATALOG, UnknownSiteError, normalize_site_code

# 基于 docs/bookmarks_2026_8_18(1).html 的 2657 个实测样本：长度 12/13/14，A 开头。
# 上限放宽到 19 位避免误拒未来更长的 ID。
SELLER_ID_RE = re.compile(r"^A[A-Z0-9]{11,19}$")

# 亚马逊各站点的 marketplaceID -> 本项目站点码，优先于域名 TLD 判定。
_MARKETPLACE_ID_TO_SITE: dict[str, str] = {
    "ATVPDKIKX0DER": "US",
    "A1F83G8C2ARO7P": "GB",
    "A1PA6795UKMFR9": "DE",
}

_DOMAIN_TO_SITE: dict[str, str] = {site.domain: code for code, site in SITE_CATALOG.items()}

REJECT_STORE_PAGE = "品牌店铺页无法提取卖家 ID"
REJECT_PRODUCT_PAGE = "商品详情页无法提取卖家 ID"
REJECT_UNRECOGNIZED = "无法识别为卖家链接或卖家 ID"
REJECT_SITE_UNKNOWN = "无法确定站点（链接未带站点信息，且未指定默认站点）"
REJECT_BARE_ID_NEEDS_SITE = "裸卖家 ID 不带站点信息，需要指定默认站点"

_DEDUP_SUFFIX_RE = re.compile(r"[（(]\s*\d+\s*[）)]\s*$")


@dataclass(frozen=True)
class ParsedSellerRef:
    """解析成功的卖家标识。"""

    seller_id: str
    site: str


@dataclass(frozen=True)
class RejectedSellerRef:
    """解析失败，附原文与原因——预览响应据此逐条展示，不静默丢弃。"""

    text: str
    reason: str


def parse_seller_ref(
    text: str, *, fallback_site: str | None = None
) -> ParsedSellerRef | RejectedSellerRef:
    """从单条文本（URL 或裸 ID）解析出卖家标识。"""
    raw = text.strip()
    if not raw:
        return RejectedSellerRef(text=raw, reason=REJECT_UNRECOGNIZED)

    if SELLER_ID_RE.match(raw):
        site = _normalize_fallback(fallback_site)
        if site is None:
            return RejectedSellerRef(text=raw, reason=REJECT_BARE_ID_NEEDS_SITE)
        return ParsedSellerRef(seller_id=raw, site=site)

    url = raw if "://" in raw else f"https://{raw}"
    parsed = urlparse(url)
    path = parsed.path or ""

    if path.startswith("/stores/"):
        return RejectedSellerRef(text=raw, reason=REJECT_STORE_PAGE)
    if path.startswith("/dp/"):
        return RejectedSellerRef(text=raw, reason=REJECT_PRODUCT_PAGE)

    query = parse_qs(parsed.query)
    seller_id = _first(query, "me") or _first(query, "seller")
    if not seller_id or not SELLER_ID_RE.match(seller_id):
        return RejectedSellerRef(text=raw, reason=REJECT_UNRECOGNIZED)

    site = _resolve_site(query, parsed.netloc, fallback_site)
    if site is None:
        return RejectedSellerRef(text=raw, reason=REJECT_SITE_UNKNOWN)

    return ParsedSellerRef(seller_id=seller_id, site=site)


def parse_bulk(
    lines: list[str], *, fallback_site: str | None = None
) -> list[ParsedSellerRef | RejectedSellerRef]:
    """逐行解析，跳过空行；不去重——重复次数由调用方（service 层）折算成 priority。"""
    return [
        parse_seller_ref(line, fallback_site=fallback_site)
        for line in lines
        if line.strip()
    ]


@dataclass(frozen=True)
class BookmarkEntry:
    """一条书签：原始 href + 拍平后的文件夹路径（从外到内）。"""

    href: str
    folder_path: tuple[str, ...]


def extract_bookmark_entries(html: str) -> list[BookmarkEntry]:
    """从 Netscape 格式书签 HTML 中提取全部 ``<A HREF>`` 及其所属文件夹路径。

    书签格式的嵌套规则：``<DT><H3>文件夹名</H3>`` 后紧跟 ``<DL><p>`` 是该文件夹的内容，
    ``</DL>`` 结束该层级。据此维护一个文件夹名栈，遇到 ``<A>`` 时取当前栈作为路径。
    """
    parser = _BookmarkHTMLParser()
    parser.feed(html)
    return parser.entries


def derive_group_name(folder_path: tuple[str, ...]) -> str:
    """把文件夹路径拍平成候选分组名：取最深一层，去掉「（3）」这类去重后缀。"""
    if not folder_path:
        return "未分类"
    name = _DEDUP_SUFFIX_RE.sub("", folder_path[-1]).strip()
    return name or "未分类"


def _first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _normalize_fallback(fallback_site: str | None) -> str | None:
    if not fallback_site:
        return None
    try:
        return normalize_site_code(fallback_site)
    except UnknownSiteError:
        return None


def _resolve_site(
    query: dict[str, list[str]], netloc: str, fallback_site: str | None
) -> str | None:
    marketplace_id = _first(query, "marketplaceID")
    if marketplace_id and marketplace_id in _MARKETPLACE_ID_TO_SITE:
        return _MARKETPLACE_ID_TO_SITE[marketplace_id]

    domain = netloc.split("@")[-1]
    if domain.startswith("www."):
        domain = domain[4:]
    if domain in _DOMAIN_TO_SITE:
        return _DOMAIN_TO_SITE[domain]

    return _normalize_fallback(fallback_site)


class _BookmarkHTMLParser(HTMLParser):
    """Netscape 书签格式的最小化解析：只关心 H3/DL/A 三种标签的嵌套关系。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[str] = []
        self._pending_folder: str | None = None
        self._in_h3 = False
        self.entries: list[BookmarkEntry] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "h3":
            self._in_h3 = True
            self._pending_folder = ""
        elif tag == "dl":
            if self._pending_folder is not None:
                self._stack.append(self._pending_folder.strip())
                self._pending_folder = None
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.entries.append(BookmarkEntry(href=href, folder_path=tuple(self._stack)))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "h3":
            self._in_h3 = False
        elif tag == "dl" and self._stack:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        if self._in_h3:
            self._pending_folder = (self._pending_folder or "") + data
