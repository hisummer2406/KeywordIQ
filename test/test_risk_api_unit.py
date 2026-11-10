"""外观专利核查接口的权限挂载、输入校验与静态约定（无库）。

没有全局鉴权中间件，漏挂依赖就是裸奔——逐个路由检查（specs/patent-risk-check「三个权限点分别把守」）。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.auth import permissions
from app.table.analysis.partitioning import PARTITIONED_TABLES
from app.table.risk import check_service, risk_api
from app.table.risk.jurisdictions import JURISDICTIONS, MARKET_RECOMMENDATIONS
from app.table.risk.risk_errors import InvalidImageError
from app.table.risk.risk_schemas import CheckCreate, EvidenceRequest, ReviewCreate
from app.table.risk.task_service import _https, _norm_biblio, _norm_legal
from migration_scope import V2_TABLES

_SEARCH = {("POST", "/checks"), ("POST", "/checks/{check_id}/pages"),
           ("POST", "/checks/{check_id}/retry"), ("POST", "/checks/{check_id}/evidence")}
_REVIEW = {("POST", "/checks/{check_id}/reviews")}


def test_every_route_requires_the_right_permission():
    seen = set()
    for route in risk_api.risk_router.routes:
        deps = {d.call for d in route.dependant.dependencies}
        for method in route.methods:
            key = (method, route.path)
            seen.add(key)
            if key in _SEARCH:
                assert risk_api._require_search in deps, key
            elif key in _REVIEW:
                assert risk_api._require_review in deps, key
            else:
                assert risk_api._require_view in deps, key
    assert _SEARCH | _REVIEW <= seen


def test_permissions_default_to_admin_only():
    for code in (permissions.RISK_VIEW, permissions.RISK_SEARCH, permissions.RISK_REVIEW):
        assert code in permissions.ROLES["admin"][1]
        assert code not in permissions.ROLES["operator"][1]
        assert code not in permissions.ROLES["analyst"][1]
        assert code not in permissions.SUPER_ONLY_PERMISSIONS


def test_tables_registered_and_not_partitioned():
    risk_tables = {t for t in V2_TABLES if t.startswith("risk_")}
    assert len(risk_tables) == 8
    assert not risk_tables & set(PARTITIONED_TABLES)


def test_sniff_image_checks_content_not_name():
    assert check_service.sniff_image(b"\xff\xd8\xff\xe0abc") == "image/jpeg"
    assert check_service.sniff_image(b"\x89PNG\r\n\x1a\nabc") == "image/png"
    for bad in (b"", b"GIF89a....", b"<svg/>", b"\x89PNG" + b"\x00" * (4096 * 1024)):
        with pytest.raises(InvalidImageError):
            check_service.sniff_image(bad)


def test_check_create_normalizes_and_rejects():
    ok = CheckCreate(product_name=" 杯 ", product_version="v1", markets=["us", "US", "gb"],
                     jurisdictions=["eu"], page_size=20, client_token="abcdefgh")
    assert ok.product_name == "杯" and ok.markets == ["US", "GB"] and ok.jurisdictions == ["EU"]
    for bad in ({"page_size": 30}, {"product_version": "  "}, {"markets": []}, {"client_token": "short"},
                {"client_token": "bad token!"}, {"extra": 1}):
        with pytest.raises(ValidationError):
            CheckCreate(**{"product_name": "杯", "product_version": "v1", "markets": ["US"],
                           "jurisdictions": ["US"], "page_size": 20, "client_token": "abcdefgh", **bad})


def test_evidence_batch_limit():
    with pytest.raises(ValidationError):
        EvidenceRequest(client_token="abcdefgh", candidate_ids=list(range(21)))
    assert EvidenceRequest(client_token="abcdefgh", candidate_ids=[3, 1, 3]).candidate_ids == [1, 3]


def test_review_enums():
    with pytest.raises(ValidationError):
        ReviewCreate(client_token="abcdefgh", market="US", risk_level="safe", decision="pass",
                     basis="x", uncovered_scope="y")
    with pytest.raises(ValidationError):
        ReviewCreate(client_token="abcdefgh", market="US", risk_level="low", decision="auto_pass",
                     basis="x", uncovered_scope="y")


def test_market_recommendations_reference_known_jurisdictions():
    for codes in MARKET_RECOMMENDATIONS.values():
        assert set(codes) <= set(JURISDICTIONS)
    assert JURISDICTIONS["EU"].verified is False  # EM 取值待真实联调核实


def test_vendor_values_sanitized():
    assert _https("javascript:alert(1)") is None and _https("http://x") is None
    assert _https("https://ok") == "https://ok"
    bib = _norm_biblio({"pn": "US1", "bibliographic_data": {
        "invention_title": [{"lang": "en", "text": "Cup"}], "parties": {"assignees": [{"name": "A"}, "bad"]},
        "publication_reference": {"country": "US", "doc_number": "D1", "kind": "S", "date": 20200101}}})
    assert bib["title"] == "Cup" and bib["assignees"] == ["A"] and bib["publication_number"] == "USD1S"
    assert _norm_biblio({})["title"] is None
    legal = _norm_legal({"legal_date": "20240101", "patent_legal": {"legal_status": ["Granted", 1]}})
    assert legal["legal_date"] == 20240101 and legal["legal_status"] == ["Granted"]
