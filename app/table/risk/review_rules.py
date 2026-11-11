"""人工意见的校验规则，#24 外观专利核查与机会中心 IP 专家共用（add-opportunity-ip-agent 决策 8）。

只校验与存储无关的业务规则；归属（市场、证据候选是否属于本记录）由调用方查库后传入结果。
"""

from __future__ import annotations

from app.table.risk.risk_errors import InvalidReviewError
from app.table.risk.risk_schemas import ReviewCreate

#: 检索未完成时不允许的人工建议（review 规格）。
DECISIONS_NEED_SEARCH = ("pass", "conditional_pass")


def validate_review(req: ReviewCreate, *, markets: list[str], evidence_owned: bool, search_done: bool) -> None:
    if req.market not in markets:
        raise InvalidReviewError("该市场不在本次核查范围内")
    if not evidence_owned:
        raise InvalidReviewError("引用的候选不属于本次核查")
    if req.decision in DECISIONS_NEED_SEARCH and not search_done:
        raise InvalidReviewError("检索尚未完成，只能选择继续验证、需补证或不建议上架")
    if req.decision == "conditional_pass" and not (
        req.conditions and req.evidence_candidate_ids and req.review_date
    ):
        raise InvalidReviewError("条件通过须填写整改条件、至少一条证据引用和复核日期")
