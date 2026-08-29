"""SellerRefParser 的纯函数单元测试，不连库。

样本取自 docs/bookmarks_2026_8_18(1).html 的真实抽样（12/13/14 位 ID 各一例，
US/GB 各一例），边界情形（marketplaceID 缺失回落域名、两种拒绝形态、裸 ID 需要
fallback_site）用构造样本——真实文件里没有这几种形态（书签清一色是 me= 商品列表页）。
"""

from app.table.competitor.seller_ref_parser import (
    REJECT_BARE_ID_NEEDS_SITE,
    REJECT_PRODUCT_PAGE,
    REJECT_SITE_UNKNOWN,
    REJECT_STORE_PAGE,
    REJECT_UNRECOGNIZED,
    ParsedSellerRef,
    RejectedSellerRef,
    derive_group_name,
    extract_bookmark_entries,
    parse_bulk,
    parse_seller_ref,
)

# 真实样本：docs/bookmarks_2026_8_18(1).html
_SAMPLE_12 = (
    "https://www.amazon.com/s?i=merchant-items&me=AS2G99DA1EUF&s=date-desc-rank"
    "&ds=v1%3ASPSuSfGPg6EKIqq2Ky18rEZt5SqQFW9XqFbL2cycEu8"
    "&marketplaceID=ATVPDKIKX0DER&qid=1786786869&ref=sr_st_date-desc-rank"
)
_SAMPLE_13 = (
    "https://www.amazon.com/s?i=merchant-items&me=AD3AZNHSPUTDG&s=date-desc-rank"
    "&ds=v1%3ArMl8LyJan6fR6oW25X3nIbvNSTd40tIcGjbRVMI%2BrGQ"
    "&marketplaceID=ATVPDKIKX0DER&qid=1782380998&ref=sr_st_date-desc-rank"
)
_SAMPLE_14 = (
    "https://www.amazon.com/s?i=merchant-items&me=A1M8X1I5TORIT0&s=date-desc-rank"
    "&ds=v1%3AG7WmQf9ZHUXO0h9PB01h6ldNnRdzZwF%2BKg6NisTfiNU"
    "&marketplaceID=ATVPDKIKX0DER&qid=1779354330&ref=sr_st_date-desc-rank"
)
_SAMPLE_GB = (
    "https://www.amazon.co.uk/s?i=merchant-items&me=A16U556F7GIJ50&s=date-desc-rank"
    "&ds=v1%3AE9ckFNxGsk6bf7EjVR71BmOGUSwYDDIBTlzEwOZ1QrU"
    "&marketplaceID=A1F83G8C2ARO7P&qid=1785897064&ref=sr_st_date-desc-rank"
)


def test_parses_real_sample_seller_ids_of_each_length():
    assert parse_seller_ref(_SAMPLE_12) == ParsedSellerRef(seller_id="AS2G99DA1EUF", site="US")
    assert parse_seller_ref(_SAMPLE_13) == ParsedSellerRef(seller_id="AD3AZNHSPUTDG", site="US")
    assert parse_seller_ref(_SAMPLE_14) == ParsedSellerRef(seller_id="A1M8X1I5TORIT0", site="US")


def test_marketplace_id_maps_uk_to_gb_not_uk():
    result = parse_seller_ref(_SAMPLE_GB)
    assert result == ParsedSellerRef(seller_id="A16U556F7GIJ50", site="GB")


def test_ds_signature_param_is_discarded_not_stored():
    result = parse_seller_ref(_SAMPLE_12)
    assert isinstance(result, ParsedSellerRef)
    # ParsedSellerRef 只有 seller_id/site 两个字段，ds= 无处可存。
    assert result == ParsedSellerRef(seller_id="AS2G99DA1EUF", site="US")


def test_sp_seller_link():
    result = parse_seller_ref("https://www.amazon.com/sp?seller=A1TZ946F8HZTNY&marketplaceID=ATVPDKIKX0DER")
    assert result == ParsedSellerRef(seller_id="A1TZ946F8HZTNY", site="US")


def test_at_a_glance_seller_link():
    result = parse_seller_ref(
        "https://www.amazon.com/gp/help/seller/at-a-glance.html"
        "?seller=A1TZ946F8HZTNY&marketplaceID=ATVPDKIKX0DER"
    )
    assert result == ParsedSellerRef(seller_id="A1TZ946F8HZTNY", site="US")


def test_marketplace_id_missing_falls_back_to_domain():
    result = parse_seller_ref("https://www.amazon.de/s?i=merchant-items&me=A1TZ946F8HZTNY")
    assert result == ParsedSellerRef(seller_id="A1TZ946F8HZTNY", site="DE")


def test_store_page_is_rejected_with_reason():
    result = parse_seller_ref("https://www.amazon.com/stores/SomeBrand/page/1234-5678-uuid")
    assert result == RejectedSellerRef(
        text="https://www.amazon.com/stores/SomeBrand/page/1234-5678-uuid",
        reason=REJECT_STORE_PAGE,
    )


def test_product_detail_page_is_rejected_with_reason():
    result = parse_seller_ref("https://www.amazon.com/dp/B0ABCDEFGH")
    assert result == RejectedSellerRef(
        text="https://www.amazon.com/dp/B0ABCDEFGH", reason=REJECT_PRODUCT_PAGE
    )


def test_unrecognized_text_is_rejected_not_raised():
    result = parse_seller_ref("just some random text")
    assert result == RejectedSellerRef(text="just some random text", reason=REJECT_UNRECOGNIZED)


def test_bare_id_without_fallback_site_is_rejected():
    result = parse_seller_ref("A1TZ946F8HZTNY")
    assert result == RejectedSellerRef(text="A1TZ946F8HZTNY", reason=REJECT_BARE_ID_NEEDS_SITE)


def test_bare_id_with_fallback_site_resolves():
    result = parse_seller_ref("A1TZ946F8HZTNY", fallback_site="us")
    assert result == ParsedSellerRef(seller_id="A1TZ946F8HZTNY", site="US")


def test_bare_id_with_unknown_fallback_site_is_rejected():
    result = parse_seller_ref("A1TZ946F8HZTNY", fallback_site="UK")
    assert result == RejectedSellerRef(text="A1TZ946F8HZTNY", reason=REJECT_BARE_ID_NEEDS_SITE)


def test_link_with_no_site_signal_and_no_fallback_is_rejected():
    result = parse_seller_ref("https://example.com/s?i=merchant-items&me=A1TZ946F8HZTNY")
    assert result == RejectedSellerRef(
        text="https://example.com/s?i=merchant-items&me=A1TZ946F8HZTNY",
        reason=REJECT_SITE_UNKNOWN,
    )


def test_parse_bulk_preserves_order_and_skips_blank_lines():
    results = parse_bulk(["A1TZ946F8HZTNY", "", "  ", _SAMPLE_12], fallback_site="US")
    assert results == [
        ParsedSellerRef(seller_id="A1TZ946F8HZTNY", site="US"),
        ParsedSellerRef(seller_id="AS2G99DA1EUF", site="US"),
    ]


def test_extract_bookmark_entries_tracks_nested_folder_path():
    html = """
    <DL><p>
        <DT><A HREF="https://www.amazon.com/s?i=merchant-items&me=ROOTLEVELID12">root</A>
        <DT><H3>竞品店铺</H3>
        <DL><p>
            <DT><H3>竞10.26</H3>
            <DL><p>
                <DT><A HREF="https://www.amazon.com/s?i=merchant-items&me=NESTEDID1234">nested</A>
            </DL><p>
        </DL><p>
    </DL><p>
    """
    entries = extract_bookmark_entries(html)
    assert entries[0].folder_path == ()
    assert entries[0].href.endswith("me=ROOTLEVELID12")
    assert entries[1].folder_path == ("竞品店铺", "竞10.26")
    assert entries[1].href.endswith("me=NESTEDID1234")


def test_derive_group_name_takes_deepest_level_and_strips_dedup_suffix():
    assert derive_group_name(("竞品店铺", "竞10.26", "8.15（1）")) == "8.15"
    assert derive_group_name(("竞品店铺", "4.17(3)")) == "4.17"
    assert derive_group_name(()) == "未分类"
