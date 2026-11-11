"""IP 专家 Agent 的业务错误（错误契约见 openspec/specs/api-error-contract）。

令牌冲突、在途任务、意见校验沿用 #24 的错误类型（``app/table/risk/risk_errors.py``），
前端按同一套码处理。
"""

from __future__ import annotations

from app.common.errors import ConflictError, NotFoundError, UnprocessableError


class ResearchNotFoundError(NotFoundError):
    """调研不存在或不属于当前用户——两者不区分，避免泄露他人记录是否存在。"""

    code = "opportunity_ip.research_not_found"
    message = "调研记录不存在"


class CandidateNotFoundError(NotFoundError):
    code = "opportunity_ip.candidate_not_found"
    message = "候选专利不属于该调研"


class ImageNotFoundError(NotFoundError):
    code = "opportunity_ip.image_not_found"
    message = "该候选还没有摘要附图"


class McpNotConfiguredError(UnprocessableError):
    code = "opportunity_ip.mcp_not_configured"
    message = "智慧芽接口未配置，请联系管理员"


class LlmNotConfiguredError(UnprocessableError):
    code = "opportunity_ip.llm_not_configured"
    message = "大模型未配置，请联系管理员；仍可手动编写检索式"


class InvalidResearchInputError(UnprocessableError):
    code = "opportunity_ip.invalid_input"
    message = "调研参数不合法"


class BudgetExceededError(UnprocessableError):
    code = "opportunity_ip.budget_exceeded"
    message = "MCP 调用预算不足"


class NothingToAnalyzeError(UnprocessableError):
    code = "opportunity_ip.nothing_to_analyze"
    message = "还没有成功执行的检索，无法生成风险分析"


class NothingToFetchError(UnprocessableError):
    code = "opportunity_ip.nothing_to_fetch"
    message = "所选候选的详情都已取得，无需再次调用"


class WorkspaceNotFoundError(NotFoundError):
    """工作空间不存在或不属于当前用户——两者不区分。"""

    code = "opportunity.workspace_not_found"
    message = "工作空间不存在"


class WorkspaceNameTakenError(ConflictError):
    code = "opportunity.workspace_name_taken"
    message = "已有同名工作空间"


class WorkspaceNotEmptyError(UnprocessableError):
    code = "opportunity.workspace_not_empty"
    message = "工作空间里还有任务，先移走再删除"


class ScheduleNotFoundError(NotFoundError):
    code = "opportunity_ip.schedule_not_found"
    message = "该调研没有定时复检"


class ScheduleQueryNotExecutedError(UnprocessableError):
    code = "opportunity_ip.schedule_query_not_executed"
    message = "定时复检只能使用本调研已成功执行过的检索式"
