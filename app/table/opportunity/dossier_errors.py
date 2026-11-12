"""机会档案的业务错误（错误契约见 openspec/specs/api-error-contract）。"""

from __future__ import annotations

from app.common.errors import ConflictError, NotFoundError, UnprocessableError


class DossierNotFoundError(NotFoundError):
    code = "opportunity_dossier.not_found"
    message = "机会档案不存在"


class DossierInputError(UnprocessableError):
    code = "opportunity_dossier.invalid_input"
    message = "档案参数不合法"


class DossierTokenConflictError(ConflictError):
    code = "opportunity_dossier.token_conflict"
    message = "重复提交的内容与上一次不一致，请刷新后重试"


class KeywordLimitError(UnprocessableError):
    code = "opportunity_dossier.keyword_limit"
    message = "一个档案最多 20 个关键词"


class KeywordNotFoundError(NotFoundError):
    code = "opportunity_dossier.keyword_not_found"
    message = "该关键词不在档案里"


class LastKeywordError(UnprocessableError):
    code = "opportunity_dossier.last_keyword"
    message = "档案至少保留一个关键词"


class KeywordGroupNotFoundError(NotFoundError):
    """词库分组不存在或不属于当前用户——两者不区分。"""

    code = "opportunity_dossier.keyword_group_not_found"
    message = "词库分组不存在"


class EvidenceNotFoundError(NotFoundError):
    code = "opportunity_dossier.evidence_not_found"
    message = "该证据不属于这个档案"


class DecisionRuleError(UnprocessableError):
    code = "opportunity_dossier.decision_rule"
    message = "决定不符合规则"


class AskInProgressError(ConflictError):
    code = "opportunity_dossier.ask_in_progress"
    message = "上一个问题还在回答中，请稍候"


class AskNotConfiguredError(UnprocessableError):
    code = "opportunity_dossier.llm_not_configured"
    message = "大模型未配置，暂时不能追问；证据、补证与决定不受影响"
