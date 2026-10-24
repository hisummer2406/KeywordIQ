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


def test_competitor_new_keys_by_user_and_scope():
    a, b = _key(user_id=1, competitor_new=True), _key(user_id=2, competitor_new=True)
    assert a != b
    assert a.user_id == 1 and "competitor:view" in a.scope
    assert a.fingerprint() != b.fingerprint()


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

    default = service.search_summary_fields(AnalysisSearchRequest(), 1)
    explicit = service.search_summary_fields(AnalysisSearchRequest(site=settings.default_site), 1)
    assert default == explicit and default["view"] == "DAY"
    assert service.search_summary_fields(AnalysisSearchRequest(site="ZZ"), 1) == {"view": "DAY"}
