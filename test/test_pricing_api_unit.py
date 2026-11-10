"""定价核算接口的权限挂载与输入校验（无库）。

没有全局鉴权中间件，漏挂依赖就是裸奔——逐个路由检查（specs/pricing-records「核价权限点」、
pricing-rate-management「规则维护权限」，PRICE-10）。
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.table.pricing import pricing_api
from app.table.pricing.pricing_schemas import PricingInput, to_jsonable

_WRITE_RULE_PATHS = {
    ("POST", "/rules"),
    ("PATCH", "/rules/{rule_id}"),
    ("DELETE", "/rules/{rule_id}"),
    ("POST", "/rules/{rule_id}/activate"),
    ("POST", "/rules/{rule_id}/end"),
    ("POST", "/rules/{rule_id}/retire"),
    ("POST", "/rules/{rule_id}/copy"),
}


def _deps(route):
    return {d.call for d in route.dependant.dependencies}


def test_every_route_requires_pricing_permission():
    seen = set()
    for route in pricing_api.pricing_router.routes:
        deps = _deps(route)
        for method in route.methods:
            key = (method, route.path)
            seen.add(key)
            if key in _WRITE_RULE_PATHS:
                assert pricing_api._require_manage in deps, key
            else:
                assert pricing_api._require_view in deps or pricing_api._require_manage in deps, key
    assert _WRITE_RULE_PATHS <= seen


def test_input_rejects_float_and_unknown_market_and_attribute():
    base = {"mode": "target_profit", "markets": ["US"], "target_profit": "40"}
    with pytest.raises(ValidationError):
        PricingInput.model_validate({**base, "purchase_cost": 10.5})
    with pytest.raises(ValidationError):
        PricingInput.model_validate({**base, "target_profit": 40.0})
    with pytest.raises(ValidationError):
        PricingInput.model_validate({**base, "markets": ["XX"]})
    with pytest.raises(ValidationError):
        PricingInput.model_validate({**base, "attributes": ["unicorn"]})
    with pytest.raises(ValidationError):
        PricingInput.model_validate({**base, "market_inputs": {"GB": {}}})
    ok = PricingInput.model_validate({**base, "markets": ["us", "US", "gb"], "purchase_cost": "10.5"})
    assert ok.markets == ["US", "GB"] and ok.purchase_cost == Decimal("10.5")


def test_to_jsonable_formats_decimal_without_exponent():
    assert to_jsonable({"a": Decimal("1E+2"), "b": [Decimal("0.10")], "c": frozenset({"x"})}) == {
        "a": "100", "b": ["0.10"], "c": ["x"]}
