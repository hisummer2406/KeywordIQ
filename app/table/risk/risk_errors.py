"""外观专利核查的业务错误（错误契约见 openspec/specs/api-error-contract）。"""

from __future__ import annotations

from app.common.errors import ConflictError, NotFoundError, UnprocessableError


class CheckNotFoundError(NotFoundError):
    """核查单不存在或不属于当前用户——两者不区分，避免泄露他人记录是否存在。"""

    code = "risk.check_not_found"
    message = "核查记录不存在"


class CandidateNotFoundError(NotFoundError):
    code = "risk.candidate_not_found"
    message = "候选专利不属于该核查记录"


class EvidenceFileNotFoundError(NotFoundError):
    """存档文件不存在、未存档或不属于当前用户——不区分，避免泄露。"""

    code = "risk.evidence_file_not_found"
    message = "存档文件不存在"


class ProviderNotConfiguredError(UnprocessableError):
    code = "risk.provider_not_configured"
    message = "智慧芽接口未配置，请联系管理员"


class InvalidImageError(UnprocessableError):
    code = "risk.invalid_image"
    message = "图片须为 JPG 或 PNG，且不超过 4 MB"


class InvalidCheckInputError(UnprocessableError):
    code = "risk.invalid_input"
    message = "核查参数不合法"


class TokenConflictError(ConflictError):
    code = "risk.token_conflict"
    message = "重复提交的请求内容不一致，请刷新后重试"


class TaskInProgressError(ConflictError):
    code = "risk.task_in_progress"
    message = "已有同类任务在执行，请等待完成"


class NothingToFetchError(UnprocessableError):
    code = "risk.nothing_to_fetch"
    message = "没有可读取的后续结果"


class InvalidReviewError(UnprocessableError):
    code = "risk.invalid_review"
    message = "审核意见不完整"
