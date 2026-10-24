"""分析查询的结果键（#154 analysis-query-coordination「结果键区分可共享与私有结果」，design D1）。

结果键是请求去重与结果缓存的唯一依据，也以指纹形式进请求摘要，供重采估算重复率（D5）。
两条纪律：

- **结果与查询者无关的请求跨用户共享同一个键。** ``search()`` 的 ``user_id`` 只喂给
  ``competitor_new``（读用户私有的关注数据），其余请求不含用户标识；含 ``competitor_new`` 时
  键带上用户标识与 ``competitor:view`` 权限范围。
- **规范化只用代码里能证明等价的规则**（见 ``analysis_crud`` 的对应判断）：布尔筛选为假与
  没传一样（``if req.first_listed``）、空列表与没传一样（``req.brands or None``）、``IN``
  列表与顺序无关、没填关键词时匹配方式无关、空标题与没传一样。拿不准的（排序缺省、日期
  缺省）保持原样——多分出一个键只少合并一次，错并一个键是返回别人的结果。

请求模型新增字段时必须在 ``_FIELDS`` 里归类，否则 ``test_result_key.py`` 失败：未归类的字段
若影响结果却没进键，两个不同的查询会被当成同一个。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.auth.permissions import ANALYSIS_VIEW, COMPETITOR_VIEW
from app.table.search.search_schemas import AnalysisSearchRequest

# 每个请求字段的规范化方式。site 单独处理（服务端解析缺省站点后传入）。
_AS_IS = "as_is"
_FLAG = "flag"            # 真值才有意义：假与 None 等价
_SET = "set"              # IN 列表：空与 None 等价、顺序无关
_TEXT = "text"            # 空串与 None 等价
_KEYWORD_MODE = "keyword_mode"  # 只在填了关键词时有意义

_FIELDS: dict[str, str] = {
    "site": _AS_IS,
    "view": _AS_IS,
    "date": _AS_IS,
    "page": _AS_IS,
    "perPage": _AS_IS,
    "include_total": _AS_IS,
    "include": _AS_IS,
    "orderBy": _AS_IS,
    "orderDir": _AS_IS,
    "keyword": _AS_IS,  # 请求模型已去空白、全空白落回 None
    "keyword_mode": _KEYWORD_MODE,
    "product_title": _TEXT,
    "asins": _SET,
    "brands": _SET,
    "categories": _SET,
    "ranking_min": _AS_IS,
    "ranking_max": _AS_IS,
    "change_min": _AS_IS,
    "change_max": _AS_IS,
    "click_share_min": _AS_IS,
    "click_share_max": _AS_IS,
    "conversion_share_min": _AS_IS,
    "conversion_share_max": _AS_IS,
    "first_listed": _FLAG,
    "competitor_new": _FLAG,
    "exclude_brand_in_keyword": _FLAG,
}

# 结果依赖查询者私有数据的筛选：命中时键带用户标识。
_PRIVATE_FLAGS = ("competitor_new",)


@dataclass(frozen=True)
class ResultKey:
    """``canonical`` 是规范化后的条件（JSON，键已排序）；``user_id`` 只在私有请求时非空。"""

    scope: tuple[str, ...]
    user_id: int | None
    canonical: str

    def fingerprint(self) -> str:
        """不可逆的短指纹，进请求摘要用；不含原始条件，私有请求的用户标识也只以哈希出现。"""
        raw = json.dumps([self.scope, self.user_id, self.canonical], ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _normalise(kind: str, value, req: AnalysisSearchRequest):
    if kind == _FLAG:
        return True if value else None
    if kind == _SET:
        return sorted(value) if value else None
    if kind == _TEXT:
        return value or None
    if kind == _KEYWORD_MODE:
        return value if req.keyword else None
    return value


def result_key(req: AnalysisSearchRequest, *, site: str, user_id: int) -> ResultKey:
    """``site`` 为服务端解析后的站点（缺省站点与显式写出得到同一个键）。"""
    values = {
        name: _normalise(kind, getattr(req, name), req) for name, kind in _FIELDS.items() if name != "site"
    }
    values["site"] = site
    private = any(getattr(req, name) for name in _PRIVATE_FLAGS)
    scope = (ANALYSIS_VIEW, COMPETITOR_VIEW) if private else (ANALYSIS_VIEW,)
    canonical = json.dumps(values, sort_keys=True, ensure_ascii=False, default=str)
    return ResultKey(scope=scope, user_id=user_id if private else None, canonical=canonical)
