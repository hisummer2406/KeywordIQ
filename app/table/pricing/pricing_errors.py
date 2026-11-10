"""定价核算的业务错误（错误契约见 openspec/specs/api-error-contract）。"""

from __future__ import annotations

from app.common.errors import ConflictError, NotFoundError, UnprocessableError


class SheetNotFoundError(NotFoundError):
    """核价单不存在或不属于当前用户——两者不区分，避免泄露他人记录是否存在。"""

    code = "pricing.sheet_not_found"
    message = "核价记录不存在"


class VersionNotFoundError(NotFoundError):
    code = "pricing.version_not_found"
    message = "核价版本不存在"


class SheetConflictError(ConflictError):
    code = "pricing.sheet_conflict"
    message = "核价记录已在其他页面修改，请刷新后再操作"


class VersionNotCurrentError(ConflictError):
    code = "pricing.version_not_current"
    message = "只能从最新保存的版本中选定方案，请先重新核价"


class OptionNotSelectableError(UnprocessableError):
    code = "pricing.option_not_selectable"
    message = "该方案不可用或费用不完整，不能选定"


class RuleNotFoundError(NotFoundError):
    code = "pricing.rule_not_found"
    message = "费用规则不存在"


class RuleInvalidError(UnprocessableError):
    code = "pricing.rule_invalid"
    message = "费用规则内容不合法"


class RuleOverlapError(ConflictError):
    code = "pricing.rule_overlap"
    message = "同一范围已有生效期重叠的启用版本，请先为旧版本设定失效日"


class RuleImmutableError(ConflictError):
    code = "pricing.rule_immutable"
    message = "已启用或停用的规则不能修改，请复制为新草稿"


class RuleConflictError(ConflictError):
    code = "pricing.rule_conflict"
    message = "费用规则已被其他人修改，请刷新后再操作"


class InputInvalidError(UnprocessableError):
    code = "pricing.input_invalid"
    message = "核价输入不合法"
