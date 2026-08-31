"""独立站解析器单元测试：不连库、不连网。

样本取自 2026-08-31 对 leteimall.com 的真实响应（``test/fixtures/dtc/``，只截断了长
数组与描述文本，字段名与类型一字未改）。**这些测试不得改成访问真实站点**——外部站点
会变化、会失败，据此写的测试既不稳定也无法归因。
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.common.errors import BadRequestError
from app.table.competitor.store_fetcher import (
    CatalogParseError,
    MetaParseError,
    RankParseError,
    normalize_store_domain,
    parse_bestseller_html,
    parse_products_json,
    parse_store_meta,
)

FIXTURES = Path(__file__).parent / "fixtures" / "dtc"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


# --------------------------------------------------------------------------
# 站点 meta：身份、币种、商品总数
# --------------------------------------------------------------------------


def test_parse_store_meta_reads_currency_and_identity():
    meta = parse_store_meta(_fixture("meta.json"))

    # 样本取自 blessrich.com——它是 GBP，不是 USD。17 个站点里已有两种币种。
    assert meta.currency == "GBP"
    assert meta.myshopify_domain == "07d525-3.myshopify.com"
    assert meta.country == "CN"
    assert meta.shop_id == 84683653408


def test_parse_store_meta_exposes_authoritative_product_count():
    """商品总数用作翻页完整性的交叉校验，实测三家店与逐页累计完全吻合。"""
    meta = parse_store_meta(_fixture("meta.json"))

    assert meta.published_products_count == 1241


def test_parse_store_meta_reads_ships_to_countries():
    meta = parse_store_meta(_fixture("meta.json"))

    assert meta.ships_to_countries == ("GB", "IE")


@pytest.mark.parametrize(
    "payload",
    [
        "<!doctype html><html><body>Shop</body></html>",
        json.dumps({"hello": "world"}),
        json.dumps({"currency": "USD"}),  # 缺 myshopify_domain
        json.dumps({"myshopify_domain": "x.myshopify.com"}),  # 缺 currency
    ],
)
def test_parse_store_meta_rejects_non_shopify(payload):
    """meta 形状不符即判定非 Shopify——不另写一套平台探测。

    只要求「能解析成 JSON」是不够的：任何站点都可能在这个路径上放别的 JSON。
    """
    with pytest.raises(MetaParseError):
        parse_store_meta(payload)


# --------------------------------------------------------------------------
# 目录解析
# --------------------------------------------------------------------------


def test_parse_products_json_reads_real_response():
    page = parse_products_json(_fixture("products_page1.json"))

    assert not page.is_empty
    assert len(page.products) == 4
    for product in page.products:
        assert product.product_id > 0
        assert product.handle


def test_parse_products_json_keeps_all_three_timestamps():
    """三个时间都要留。新品判定不看它们，但出问题时要靠它们分辨。"""
    product = parse_products_json(_fixture("products_page1.json")).products[0]

    assert product.published_at is not None
    assert product.created_at is not None
    assert product.updated_at is not None
    # 带时区，不是裸 naive 时间——写库时按 timestamptz 存
    assert product.published_at.tzinfo is not None


def test_parse_products_json_expands_variants_with_prices():
    """价格挂在变体上，不是商品上。多变体商品必须逐个展开。"""
    products = parse_products_json(_fixture("products_page1.json")).products
    multi = max(products, key=lambda p: len(p.variants))

    assert len(multi.variants) > 1
    priced = [v for v in multi.variants if v.price is not None]
    assert priced, "至少要解析出一个带价格的变体"
    assert all(isinstance(v.price, Decimal) for v in priced)
    assert all(isinstance(v.variant_id, int) for v in multi.variants)


def test_parse_products_json_missing_price_is_none_not_zero():
    """缺价格返回 None 而不是 0——0 会被当成免费，静默产生一条假的降价记录。"""
    payload = {
        "products": [
            {
                "id": 1,
                "handle": "x",
                "title": "X",
                "variants": [{"id": 11, "title": "d", "price": None, "compare_at_price": ""}],
            }
        ]
    }
    variant = parse_products_json(json.dumps(payload)).products[0].variants[0]

    assert variant.price is None
    assert variant.compare_at_price is None


def test_parse_products_json_empty_page_is_not_an_error():
    """空页是翻页自然终止的正向信号，必须与形状不符区分开。"""
    page = parse_products_json(_fixture("products_empty_page.json"))

    assert page.is_empty
    assert page.products == ()


def test_parse_products_json_rejects_html_response():
    """商户关掉端点时返回的是 HTML。当成空页处理，表现就是「商品全下架了」。"""
    with pytest.raises(CatalogParseError):
        parse_products_json("<!doctype html><html><body>Not found</body></html>")


def test_parse_products_json_rejects_json_without_products_array():
    with pytest.raises(CatalogParseError):
        parse_products_json(json.dumps({"errors": "Not Found"}))


def test_parse_products_json_skips_rows_missing_primary_key_fields():
    """缺 id/handle 的行跳过而不是整页失败——它们是主键，本来也入不了库。"""
    payload = {
        "products": [
            {"title": "缺 id 与 handle"},
            {"id": 2, "handle": "ok", "title": "正常", "variants": []},
        ]
    }
    page = parse_products_json(json.dumps(payload))

    assert [p.handle for p in page.products] == ["ok"]


def test_parse_products_json_accepts_tags_as_list_or_string():
    """实测 tags 既可能是数组也可能是逗号分隔字符串。"""
    as_list = {"products": [{"id": 1, "handle": "a", "tags": ["x", "y"]}]}
    as_str = {"products": [{"id": 1, "handle": "a", "tags": "x, y"}]}

    assert parse_products_json(json.dumps(as_list)).products[0].tags == ("x", "y")
    assert parse_products_json(json.dumps(as_str)).products[0].tags == ("x", "y")


# --------------------------------------------------------------------------
# 畅销榜解析
# --------------------------------------------------------------------------


def test_parse_bestseller_html_order_is_the_rank():
    """出现顺序即店内名次，且必须与录下的期望顺序完全一致。"""
    handles = parse_bestseller_html(_fixture("bestselling_page1.html"))
    expected = tuple(json.loads(_fixture("bestselling_expected_order.json")))

    assert handles == expected


def test_parse_bestseller_html_dedupes_repeated_links():
    """实测每个 handle 在一页里出现 3 次（图片、标题及另一处），必须只认首次出现。"""
    handles = parse_bestseller_html(_fixture("bestselling_page1.html"))

    assert len(handles) == len(set(handles))


def test_parse_bestseller_html_zero_products_is_a_parse_failure():
    """取到页面却一个商品都没有 = 解析器坏了，不是「这店没商品」。

    名次只在目录非空时才拉（见 store_service），所以这个断言成立。当成空榜单的表现是
    「这家店的销量榜突然空了」，而没有任何地方会报错。
    """
    with pytest.raises(RankParseError):
        parse_bestseller_html(_fixture("bestselling_no_products.html"))


def test_parse_bestseller_html_ignores_non_product_links():
    html = """
    <a href="/collections/all">All</a>
    <a href="/products/alpha">A</a>
    <a href="/pages/about">About</a>
    <a href="/products/beta">B</a>
    """
    assert parse_bestseller_html(html) == ("alpha", "beta")


# --------------------------------------------------------------------------
# 域名规范化
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "example.com",
        "www.example.com",
        "https://example.com",
        "https://www.example.com/",
        "HTTPS://WWW.Example.COM/collections/all?sort_by=best-selling",
        "  example.com  ",
        "example.com:443",
        "example.com.",
    ],
)
def test_normalize_store_domain_collapses_all_pasted_forms(raw):
    assert normalize_store_domain(raw) == "example.com"


def test_normalize_store_domain_keeps_subdomains():
    """de.molooco.com 这类子域是独立的站点，不能被当成 molooco.com。"""
    assert normalize_store_domain("https://de.molooco.com/") == "de.molooco.com"


def test_normalize_store_domain_rejects_amazon_with_a_useful_reason():
    """亚马逊链接是最可能被误粘进来的东西，报「不是合法域名」会让用户以为链接坏了。"""
    with pytest.raises(BadRequestError) as exc:
        normalize_store_domain("https://www.amazon.com/s?i=merchant-items&me=A1TZ946F8HZTNY")

    assert "亚马逊" in str(exc.value.message)


@pytest.mark.parametrize("raw", ["", "   ", "not a domain", "http://", "localhost", "..."])
def test_normalize_store_domain_rejects_malformed_input(raw):
    with pytest.raises(BadRequestError):
        normalize_store_domain(raw)
