"""独立站（Shopify）的取数层：解析（纯函数）+ 拉取（异步 HTTP）。**不连数据库。**

分两段，边界是有意的：

- ``parse_*`` **是纯函数**，输入响应文本、输出结构化数据，不做任何 I/O。与
  ``seller_ref_parser`` 同一模式，单元测试用固定样本（``test/fixtures/dtc/``）驱动。
  **测试不得访问真实站点**：外部站点会变化、会失败，据此写的测试既不稳定也无法归因。
- ``fetch_store`` 负责翻页、重试、节奏与完整性判定，``httpx.AsyncClient`` 由调用方注入
  ——测试用 ``httpx.MockTransport`` 喂上面那些样本，同样不出网。

数据库写入与状态回写在 ``store_service`` 里，本模块一行 SQL 都没有；分层理由见
``openspec/changes/add-competitor-dtc-monitoring/design.md`` 决策 8。

**两个数据源的实测形态**（2026-08-31，取自 leteimall.com 等 17 个站点）：

- ``/products.json?limit=250&page=N``：公开 JSON，直接给 ``published_at``（上架时间，
  精确到分钟）、``created_at`` 与每个变体的 ``price``/``compare_at_price``/``sku``/
  ``available``。翻页到空页自然终止（实测一家 1202 个商品、5 页取完）。
- ``/collections/all?sort_by=best-selling``：**只有 HTML 版生效**——同名的 JSON 端点
  实测忽略 ``sort_by``（``best-selling``/``created-descending``/``manual`` 返回同一
  字母序），所以名次只能从 HTML 里按商品链接的出现顺序读。每页 20 个商品，
  **每个 handle 在页面上出现 3 次**（图片、标题及另一处），去重是必须的。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

import httpx

from app.common.errors import BadRequestError

logger = logging.getLogger(__name__)

# 商品链接形如 /products/<handle>，handle 是小写短横线串。名次即其首次出现的顺序。
_PRODUCT_HANDLE_RE = re.compile(r"/products/([a-z0-9][a-z0-9\-_%]{2,80})")

_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)
_DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")

REJECT_EMPTY_DOMAIN = "请填写独立站域名"
REJECT_MALFORMED_DOMAIN = "不是合法的域名"
REJECT_AMAZON_DOMAIN = "这是亚马逊链接，请在竞对卖家清单里添加，不要作为独立站"


class StoreParseError(Exception):
    """响应内容与预期形态不符。由拉取层捕获并转成站点状态，不直接面向用户。"""


class CatalogParseError(StoreParseError):
    """商品目录响应不是预期的 Shopify JSON。

    最常见的成因是商户关闭了 ``/products.json``——此时返回的是 404 页面或店铺主页的
    HTML，而不是 JSON。这与「抓取失败」是两回事，必须分开呈现（商户的选择 vs 故障）。
    """


class MetaParseError(StoreParseError):
    """``/meta.json`` 不是预期形态——**这同时就是「不是 Shopify 站点」的判据**。

    不另写一套平台探测（比如看 ``powered-by`` 响应头）：那需要多发一次请求，且响应头
    可被 CDN 改写。``/meta.json`` 一次请求同时回答三件事——是不是 Shopify、币种是什么、
    店里到底有多少商品。
    """


class RankParseError(StoreParseError):
    """畅销榜页面取到了，但一个商品链接都没提取出来。

    **这一律按解析失败处理，不当作「这家店没有商品」。** 之所以能这样断言，是因为
    名次拉取只在目录非空时才发起（见 ``store_service``）——目录里有商品、畅销榜却一个
    都没有，只可能是页面结构变了。把它当成空榜单，表现就是「这家店的销量榜突然空了」，
    而没有任何地方会报错。
    """


@dataclass(frozen=True)
class ParsedStoreMeta:
    """``/meta.json`` 的内容：站点身份、币种、商品总数。

    **``currency`` 不是可选的锦上添花。** 实测 17 个站点里已经出现两种币种
    （leteimall/kydschoice 是 USD，blessrich 是 GBP），价格数字本身不带币种；
    不存币种就会让 ``£12.99`` 和 ``$18.98`` 在界面上并排显示成同一个数。而且价格历史
    一旦入库，**事后补币种是补不回来的**——不知道当时那个数是哪国钱。

    ``myshopify_domain`` 是站点的稳定身份：商户换自定义域名时它不变，可用于发现
    「同一家店换了域名」。主键仍用用户认得的自定义域名。

    ``published_products_count`` 是权威的商品总数，用作翻页完整性的交叉校验——比
    「翻到空页」强得多。实测三家店（1202 / 151 / 1241）与逐页累计**完全吻合**。
    """

    shop_id: int | None
    name: str | None
    currency: str | None
    country: str | None
    myshopify_domain: str | None
    published_products_count: int | None
    ships_to_countries: tuple[str, ...]


@dataclass(frozen=True)
class ParsedVariant:
    """一个变体。价格挂在这一层——``price`` 是变体属性，不是商品属性。"""

    variant_id: int
    title: str
    sku: str | None
    price: Decimal | None
    compare_at_price: Decimal | None
    available: bool


@dataclass(frozen=True)
class ParsedProduct:
    """一个商品。

    三个时间都保留：``published_at`` 是数据源给的上架时间，``created_at`` 是创建时间，
    首次见到的时间由写入层补。新品判定 **不看** 这里的任何一个（商户批量重新发布会让
    ``published_at`` 整体跳到当日），只看商品是否已在库——三个都存是为了出问题时能分辨。
    """

    product_id: int
    handle: str
    title: str
    vendor: str | None
    product_type: str | None
    tags: tuple[str, ...]
    published_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None
    image_url: str | None
    variants: tuple[ParsedVariant, ...]


@dataclass(frozen=True)
class ParsedCatalogPage:
    """一页目录。``is_empty`` 为真即翻页应当自然终止——这是完整性判定的正向信号。"""

    products: tuple[ParsedProduct, ...]

    @property
    def is_empty(self) -> bool:
        return not self.products


def normalize_store_domain(raw: str) -> str:
    """把用户粘贴的任意形态收敛成规范域名，无法收敛时抛错并说明原因。

    接受 ``https://www.example.com/collections/all?x=1``、``www.Example.com/``、
    ``example.com`` 等形态，一律得到 ``example.com``。

    亚马逊链接单独拒绝并给出去处——它是最可能被误粘进来的东西，笼统报「不是合法域名」
    会让用户以为链接坏了，而实际上只是加错了地方。
    """
    text = (raw or "").strip()
    if not text:
        raise BadRequestError(REJECT_EMPTY_DOMAIN)

    text = _SCHEME_RE.sub("", text)
    # 路径、查询串、锚点、端口一律丢弃：我们只要主机名
    text = re.split(r"[/?#]", text, maxsplit=1)[0]
    text = text.split(":", 1)[0]
    text = text.strip().lower().rstrip(".")
    if text.startswith("www."):
        text = text[4:]

    if not text:
        raise BadRequestError(REJECT_EMPTY_DOMAIN)
    if "amazon." in text:
        raise BadRequestError(REJECT_AMAZON_DOMAIN)
    if not _DOMAIN_RE.match(text):
        raise BadRequestError(REJECT_MALFORMED_DOMAIN)
    return text


def _to_decimal(value: object) -> Decimal | None:
    """Shopify 的价格是字符串（``"18.98"``）。缺失或不可解析时返回 None 而不是 0。

    0 会被当成「免费」参与后续的调价比对，静默产生一条假的降价记录。
    """
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _to_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_tags(value: object) -> tuple[str, ...]:
    """``tags`` 实测既可能是数组也可能是逗号分隔的字符串，两种都收。"""
    if isinstance(value, list):
        return tuple(str(t).strip() for t in value if str(t).strip())
    if isinstance(value, str):
        return tuple(t.strip() for t in value.split(",") if t.strip())
    return ()


def _parse_variant(raw: dict) -> ParsedVariant | None:
    variant_id = raw.get("id")
    if not isinstance(variant_id, int):
        return None
    return ParsedVariant(
        variant_id=variant_id,
        title=str(raw.get("title") or ""),
        sku=(str(raw["sku"]).strip() or None) if raw.get("sku") else None,
        price=_to_decimal(raw.get("price")),
        compare_at_price=_to_decimal(raw.get("compare_at_price")),
        available=bool(raw.get("available")),
    )


def _parse_product(raw: dict) -> ParsedProduct | None:
    product_id = raw.get("id")
    handle = raw.get("handle")
    if not isinstance(product_id, int) or not isinstance(handle, str) or not handle:
        return None

    images = raw.get("images")
    image_url = None
    if isinstance(images, list) and images and isinstance(images[0], dict):
        src = images[0].get("src")
        image_url = str(src) if src else None

    raw_variants = raw.get("variants")
    variants = tuple(
        v
        for v in (
            _parse_variant(rv) for rv in (raw_variants if isinstance(raw_variants, list) else [])
            if isinstance(rv, dict)
        )
        if v is not None
    )

    return ParsedProduct(
        product_id=product_id,
        handle=handle,
        title=str(raw.get("title") or ""),
        vendor=(str(raw["vendor"]).strip() or None) if raw.get("vendor") else None,
        product_type=(
            (str(raw["product_type"]).strip() or None) if raw.get("product_type") else None
        ),
        tags=_parse_tags(raw.get("tags")),
        published_at=_to_datetime(raw.get("published_at")),
        created_at=_to_datetime(raw.get("created_at")),
        updated_at=_to_datetime(raw.get("updated_at")),
        image_url=image_url,
        variants=variants,
    )


def parse_store_meta(text: str) -> ParsedStoreMeta:
    """解析 ``/meta.json``。形状不符即判定该站点不是 Shopify，抛 ``MetaParseError``。

    判据取 ``currency`` 与 ``myshopify_domain`` 两个字段同时存在——单看 JSON 能解析
    是不够的，任何站点都可能在这个路径上放一个别的 JSON。
    """
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise MetaParseError("meta 响应不是 JSON") from exc

    if not isinstance(payload, dict):
        raise MetaParseError("meta 响应不是 JSON 对象")
    if not payload.get("currency") or not payload.get("myshopify_domain"):
        raise MetaParseError("meta 响应缺少 currency/myshopify_domain，判定为非 Shopify 站点")

    ships = payload.get("ships_to_countries")
    count = payload.get("published_products_count")
    shop_id = payload.get("id")

    return ParsedStoreMeta(
        shop_id=shop_id if isinstance(shop_id, int) else None,
        name=(str(payload["name"]).strip() or None) if payload.get("name") else None,
        currency=str(payload["currency"]).strip().upper()[:8],
        country=(str(payload["country"]).strip().upper()[:8] or None)
        if payload.get("country")
        else None,
        myshopify_domain=str(payload["myshopify_domain"]).strip().lower(),
        published_products_count=count if isinstance(count, int) and count >= 0 else None,
        ships_to_countries=tuple(
            str(c).strip().upper() for c in ships if str(c).strip()
        )
        if isinstance(ships, list)
        else (),
    )


def parse_products_json(text: str) -> ParsedCatalogPage:
    """解析一页 ``/products.json`` 响应。

    形状不符（商户关掉了端点、返回 HTML）时抛 ``CatalogParseError``——**空页与形状不符
    必须分开**：空页是翻页自然终止的正向信号，形状不符是端点不可用。把后者当成空页，
    表现就是「这家店的商品全下架了」。

    单个商品缺少 ``id``/``handle`` 时跳过该条而不是整页失败：一条脏数据不该让整页
    白抓，而缺这两个字段的行本来也无法入库（它们是主键）。
    """
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise CatalogParseError("响应不是 JSON") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("products"), list):
        raise CatalogParseError("响应 JSON 里没有 products 数组")

    products = tuple(
        p
        for p in (_parse_product(rp) for rp in payload["products"] if isinstance(rp, dict))
        if p is not None
    )
    return ParsedCatalogPage(products=products)


def parse_bestseller_html(html: str) -> tuple[str, ...]:
    """从畅销榜页面按出现顺序提取去重后的商品 handle，顺序即店内名次。

    靠 ``/products/<handle>`` 链接的出现顺序，**不依赖 class 名**——主题的 class 名各不
    相同且会随主题更新变化，而商品链接的路径形态是 Shopify 平台约定。

    实测每个 handle 在一页里出现 3 次（图片、标题及另一处），故必须去重且只认首次出现
    的位置。取到页面却一个都没提取出来时抛 ``RankParseError``，理由见该异常的说明。
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for match in _PRODUCT_HANDLE_RE.finditer(html or ""):
        handle = match.group(1)
        if handle not in seen:
            seen.add(handle)
            ordered.append(handle)

    if not ordered:
        raise RankParseError("畅销榜页面里没有提取到任何商品链接")
    return tuple(ordered)


# ---------------------------------------------------------------------------
# 网络拉取
#
# **必须 await 在事件循环上，不要通过 run_in_threadpool 提交。** 那 32 条线程
# （config.THREAD_POOL_LIMIT）由 CSV 导出、健康检查探活、bcrypt 密码校验（每次约
# 247ms）与同步后台导入共用；一家店抓 8 秒就占住一条线程 8 秒，站点数涨上去会实实在在
# 地拖慢登录。旁边 import_queue 的导入接手走线程池，是因为导入本身同步阻塞，本模块不是。
# ---------------------------------------------------------------------------

FETCH_OK = "OK"
FETCH_INCOMPLETE = "INCOMPLETE"
FETCH_FAILED = "FAILED"
FETCH_NO_CATALOG_API = "NO_CATALOG_API"

_PAGE_SIZE = 250
_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 1.5


@dataclass(frozen=True)
class StoreFetchOutcome:
    """一次站点拉取的全部产出与状态。

    ``catalog_complete`` 是**下架判定的唯一前提**：为假时调用方只做并集写入，绝不把
    库里有、本次没见到的商品判为下架。一次网络抖动把整店判成清仓，是这条路唯一能造成
    大面积假数据的方式。

    ``catalog_complete`` 与 ``rank_ok`` **相互独立**：畅销榜解析失败不该阻止新品与价格
    入库，反之亦然。
    """

    meta: ParsedStoreMeta | None
    products: tuple[ParsedProduct, ...]
    catalog_complete: bool
    products_json_ok: bool
    rank_handles: tuple[str, ...]
    rank_ok: bool
    status: str
    error: str | None = None


def _is_client_error(exc: Exception) -> bool:
    """4xx —— 明确的「这个地址没有东西」，不是抖动。

    最常见的就是商户关掉 ``/products.json`` 之后的 404。重试它毫无意义（三次 404 还是
    404），而且会把一次确定的答复拖成三倍的等待。
    """
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and 400 <= exc.response.status_code < 500
    )


async def _get_text(
    client: httpx.AsyncClient, url: str, *, backoff_seconds: float = _RETRY_BACKOFF_SECONDS
) -> str:
    """取一个 URL，失败重试。重试耗尽时抛原始异常，由调用方转成拉取状态。

    **4xx 不重试**：它是确定的答复而非抖动，见 ``_is_client_error``。
    """
    last_exc: Exception | None = None
    for attempt in range(_RETRIES + 1):
        try:
            response = await client.get(url)
            response.raise_for_status()
            return response.text
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            last_exc = exc
            if _is_client_error(exc):
                break
            if attempt < _RETRIES:
                await asyncio.sleep(backoff_seconds * (attempt + 1))
    assert last_exc is not None
    raise last_exc


async def _sleep_between_requests(delay_ms: int) -> None:
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000)


async def probe_store_meta(
    client: httpx.AsyncClient, domain: str, *, retry_backoff_seconds: float = _RETRY_BACKOFF_SECONDS
) -> ParsedStoreMeta:
    """探测一个站点的 ``/meta.json``，用于加入站点时判定它是不是 Shopify。

    与 ``fetch_store`` 里那次 meta 拉取的区别在**失败的处置**：这里失败即拒绝加入
    （抛异常），那里失败只是沿用库中既有信息。加入时就判定，是为了不让「不受支持的
    站点」以「抓取失败」的面目出现——用户会去重试、去等，而不是去换一个站点。
    """
    return parse_store_meta(
        await _get_text(client, f"https://{domain}/meta.json", backoff_seconds=retry_backoff_seconds)
    )


async def fetch_store(
    client: httpx.AsyncClient,
    domain: str,
    *,
    max_pages: int,
    rank_top_n: int,
    delay_ms: int,
    retry_backoff_seconds: float = _RETRY_BACKOFF_SECONDS,
) -> StoreFetchOutcome:
    """拉取一个站点的 meta、全量目录与畅销名次。

    翻页在**拿到空页时自然终止**。撞上 ``max_pages`` 仍有内容、或与 meta 声称的商品
    总数对不上，都判为不完整——后者是比前者硬得多的校验（实测三家店逐页累计与
    ``published_products_count`` 完全吻合）。

    名次**只在目录非空时才拉**。这个不变量正是 ``RankParseError`` 能被当作「解析器坏了」
    的依据：目录里有商品、畅销榜却一个都提取不出来，只可能是页面结构变了。
    """
    meta: ParsedStoreMeta | None = None
    try:
        meta = parse_store_meta(
            await _get_text(
                client, f"https://{domain}/meta.json", backoff_seconds=retry_backoff_seconds
            )
        )
    except (httpx.HTTPError, MetaParseError) as exc:
        # meta 失败不致命：站点已在库里说明加入时验过是 Shopify，这里多半是一次抖动。
        # 保留库里既有的币种等信息，不因一次失败就把它们清掉。
        logger.warning("独立站 %s 的 meta 拉取失败，沿用库中既有信息：%s", domain, exc)
    await _sleep_between_requests(delay_ms)

    products: list[ParsedProduct] = []
    catalog_complete = False
    products_json_ok = True
    status = FETCH_OK
    error: str | None = None

    page = 1
    while page <= max_pages:
        url = f"https://{domain}/products.json?limit={_PAGE_SIZE}&page={page}"
        try:
            text = await _get_text(client, url, backoff_seconds=retry_backoff_seconds)
        except httpx.HTTPError as exc:
            if page == 1 and _is_client_error(exc):
                # **404 是商户关掉 /products.json 的最常见形态**，比「200 但返回 HTML」
                # 常见得多。把它报成抓取失败，用户会看到「连续 N 次失败」并以为是暂时
                # 故障在等，而实际上这个端点永久没有了。
                products_json_ok = False
                status, error = FETCH_NO_CATALOG_API, f"目录端点不可用：{exc}"
            else:
                status, error = FETCH_FAILED, f"目录第 {page} 页拉取失败：{exc}"
            break
        try:
            parsed = parse_products_json(text)
        except CatalogParseError as exc:
            if page == 1:
                # 另一种形态：200 但返回的是店铺主页 HTML 而不是 JSON。
                products_json_ok = False
                status, error = FETCH_NO_CATALOG_API, str(exc)
            else:
                status, error = FETCH_FAILED, f"目录第 {page} 页解析失败：{exc}"
            break
        if parsed.is_empty:
            catalog_complete = True  # 翻到空页 = 自然终止
            break
        products.extend(parsed.products)
        page += 1
        await _sleep_between_requests(delay_ms)
    else:
        # while 正常跑完 = 撞上页数上限而最后一页仍有内容。
        # **必须判为不完整**：当成正常结束的话，上限之外的商品会被安静地判成下架。
        status = FETCH_INCOMPLETE
        error = f"翻页达到上限 {max_pages} 页仍未取完，本次不做下架判定"

    # meta 声称的商品总数是比「翻到空页」硬得多的完整性校验。只在实际取到的**少于**
    # 声称值时判不完整——多出来可能是拉取期间商户又发布了商品，不算问题。
    if catalog_complete and meta is not None and meta.published_products_count is not None:
        if len(products) < meta.published_products_count:
            catalog_complete = False
            status = FETCH_INCOMPLETE
            error = (
                f"取到 {len(products)} 个商品，少于站点声称的 "
                f"{meta.published_products_count} 个，本次不做下架判定"
            )

    rank_handles: tuple[str, ...] = ()
    rank_ok = False
    if products:
        rank_handles, rank_ok, rank_error = await _fetch_rank(
            client,
            domain,
            rank_top_n=rank_top_n,
            delay_ms=delay_ms,
            retry_backoff_seconds=retry_backoff_seconds,
        )
        if rank_error and error is None:
            error = rank_error

    return StoreFetchOutcome(
        meta=meta,
        products=tuple(products),
        catalog_complete=catalog_complete,
        products_json_ok=products_json_ok,
        rank_handles=rank_handles,
        rank_ok=rank_ok,
        status=status,
        error=error,
    )


async def _fetch_rank(
    client: httpx.AsyncClient,
    domain: str,
    *,
    rank_top_n: int,
    delay_ms: int,
    retry_backoff_seconds: float = _RETRY_BACKOFF_SECONDS,
) -> tuple[tuple[str, ...], bool, str | None]:
    """拉畅销榜前 N 名。

    只有 HTML 版有效——同名的 ``/collections/all/products.json`` 实测**忽略**
    ``sort_by``（三种排序返回同一字母序）。实测每页 20 个商品，取前 100 名要翻 5 页。
    """
    ordered: list[str] = []
    seen: set[str] = set()
    page = 1
    while len(ordered) < rank_top_n and page <= max(1, rank_top_n):
        url = f"https://{domain}/collections/all?sort_by=best-selling&page={page}"
        try:
            handles = parse_bestseller_html(
                await _get_text(client, url, backoff_seconds=retry_backoff_seconds)
            )
        except RankParseError as exc:
            if page == 1:
                return (), False, f"畅销榜解析失败：{exc}"
            break  # 后续页没有商品 = 榜单到头了，不是解析失败
        except httpx.HTTPError as exc:
            if page == 1:
                return (), False, f"畅销榜拉取失败：{exc}"
            break
        fresh = [h for h in handles if h not in seen]
        if not fresh:
            break  # 翻到重复内容 = 没有更多页了
        for handle in fresh:
            seen.add(handle)
            ordered.append(handle)
        page += 1
        await _sleep_between_requests(delay_ms)

    return tuple(ordered[:rank_top_n]), True, None
