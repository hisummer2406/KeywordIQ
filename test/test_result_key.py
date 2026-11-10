"""分析查询结果键（#154 analysis-query-coordination「结果键区分可共享与私有结果」）。无需 PostgreSQL。"""

from __future__ import annotations

from app.table.analysis import result_key as rk
from app.table.search.search_schemas import AnalysisSearchRequest


def _key(user_id: int = 1, site: str = "US", **fields):
    return rk.result_key(AnalysisSearchRequest(**fields), site=site, user_id=user_id)


def test_every_request_field_is_classified():
    """请求模型新增字段必须在 _FIELDS 里归类：漏掉一个影响结果的字段，两个不同的查询会共用一个键。"""
    assert set(rk._FIELDS) == set(AnalysisSearchRequest.model_fields)
    assert set(rk._PRIVATE_FLAGS) <= set(rk._FIELDS)


def test_non_private_requests_share_a_key_across_users():
    assert _key(user_id=1, first_listed=True) == _key(user_id=2, first_listed=True)
    assert _key(user_id=1).user_id is None


def test_no_private_filters_remain_after_competitor_new_retired():
    """#166：唯一的私有筛选已下架，所有请求跨用户共享键；false 与不传同键。"""
    assert rk._PRIVATE_FLAGS == {}
    assert _key(user_id=1, competitor_new=False) == _key(user_id=2)
    assert _key(user_id=1, competitor_new=False).scope == ("analysis:view",)


def test_registered_private_filter_keys_by_user_and_scope(monkeypatch):
    """登记机制保留：以后新增私有筛选只需登记，键自动按用户与权限范围区分。"""
    # 借一个现有布尔筛选当「私有筛选」样本（first_listed 已被请求模型折叠，不能再当样本）。
    monkeypatch.setitem(rk._PRIVATE_FLAGS, "exclude_brand_in_keyword", "x:view")
    a = _key(user_id=1, exclude_brand_in_keyword=True)
    b = _key(user_id=2, exclude_brand_in_keyword=True)
    assert a != b
    assert a.user_id == 1 and "x:view" in a.scope
    assert a.fingerprint() != b.fingerprint()
    # 未命中私有筛选时仍跨用户共享
    assert _key(user_id=1) == _key(user_id=2)


def test_private_filter_without_user_id_is_refused(monkeypatch):
    """宁可失败也不能把私有结果算成共享键。"""
    import pytest

    monkeypatch.setitem(rk._PRIVATE_FLAGS, "exclude_brand_in_keyword", "x:view")
    with pytest.raises(ValueError):
        rk.result_key(AnalysisSearchRequest(exclude_brand_in_keyword=True), site="US")


def test_explicit_defaults_and_equivalent_spellings_share_a_key():
    base = _key()
    assert _key(view="DAY", page=1, perPage=50, include="ALL", include_total=True) == base
    # 布尔筛选为假与没传一样；空列表、空标题与没传一样（见 analysis_crud 的真值判断）
    assert _key(first_listed=False, competitor_new=False, exclude_brand_in_keyword=False) == base
    assert _key(brands=[], categories=[], product_title="") == base
    # 没填关键词时匹配方式无关
    assert _key(keyword_mode="PHRASE") == base
    # IN 列表与顺序无关
    assert _key(brands=["b", "a"]) == _key(brands=["a", "b"])


def test_differences_that_change_results_change_the_key():
    base = _key()
    for fields in (
        {"site": "DE"}, {"view": "WEEK"}, {"page": 2}, {"perPage": 100}, {"orderBy": "x"},
        {"include": "LISTED"}, {"include_total": False}, {"first_listed": True},
        {"exclude_brand_in_keyword": True}, {"brands": ["a"]}, {"ranking_max": 10},
        {"keyword": "coffee"}, {"date": "2026-10-20"},
    ):
        site = fields.pop("site", "US")
        assert _key(site=site, **fields) != base, fields
    # 填了关键词时匹配方式要区分
    assert _key(keyword="coffee maker") != _key(keyword="coffee maker", keyword_mode="PHRASE")


def test_fingerprint_is_short_and_hides_conditions():
    key = _key(keyword="secret term", asins=["B00SECRET1"])
    fp = key.fingerprint()
    assert len(fp) == 16 and "secret" not in fp.lower()
    assert fp == _key(keyword="secret term", asins=["B00SECRET1"]).fingerprint()


def test_summary_fields_resolve_default_site_and_skip_invalid_site():
    from app.table.analysis import analysis_service as service
    from config import settings

    default = service.search_summary_fields(AnalysisSearchRequest())
    explicit = service.search_summary_fields(AnalysisSearchRequest(site=settings.default_site))
    assert default == explicit and default["view"] == "DAY"
    assert service.search_summary_fields(AnalysisSearchRequest(site="ZZ")) == {"view": "DAY"}


def test_first_listing_modes_key_by_effective_mode():
    """#178：旧 first_listed=true 与 MONITOR 是同一结果、同一个键；PERIOD 是另一个键。"""
    monitor = _key(first_listing="MONITOR")
    assert _key(first_listed=True) == monitor
    assert _key(first_listed=True, first_listing="MONITOR") == monitor
    assert _key(first_listing="PERIOD") not in (monitor, _key())
