"""业务异常体系：所有可预见的失败都从这里派生。

两条纪律（见 openspec/changes/unify-api-error-contract/design.md 决策 1、2）：

1. **业务错误一律「抛」不「返」。** 返值式的失败必须靠每个调用点自觉检查，漏检不报错、
   只静默——用户屏吞掉「用户名已存在」正是这个机制。异常无法被无声忽略。
2. **错误码与 HTTP 状态码分离。** ``code`` 是 ``<域>.<原因>`` 的语义化字符串，供日志检索与
   前端分支；``http_status`` 由异常的语义类别决定。二者独立演进：同一状态码可对应多个码。
"""

from __future__ import annotations

from typing import Final


class APIError(Exception):
    """业务异常基类。全局处理器据此产出统一错误响应。

    子类通过类属性声明语义类别（``http_status``）与默认码/文案，实例化时可覆盖 ``message``。
    ``message`` 面向用户、可直接展示——**不要把异常栈、SQL、路径、object key 写进来**。
    """

    http_status: int = 500
    code: str = "internal.error"
    message: str = "服务器内部错误"
    #: 预期内、可重试的保护性拒绝（如分析查询排队超时、执行预算到点）：拥堵时逐请求出现，
    #: 记 WARNING 不打栈，且按窗口去重（``app/common/log_limiter.py``）。真正的故障保持 False。
    log_deduped: bool = False

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        log_detail: str | None = None,
    ):
        self.message = message or type(self).message
        self.code = code or type(self).code
        #: 诊断细节，**只进日志、永不序列化进响应**。配置项名、object key、内部路径这类
        #: 排查时有用但不该给客户端看的信息放这里，而不是塞进 ``message``。
        self.log_detail = log_detail
        super().__init__(self.message)


class BadRequestError(APIError):
    """请求本身不合法（格式、组合、无法解析）。"""

    http_status = 400
    code = "request.bad"
    message = "请求不合法"


class UnauthorizedError(APIError):
    """未认证：没有凭证或凭证无效/过期。"""

    http_status = 401
    code = "auth.unauthorized"
    message = "未认证或登录已过期"


class ForbiddenError(APIError):
    """已认证但权限不足。"""

    http_status = 403
    code = "auth.forbidden"
    message = "没有执行该操作的权限"


class SessionInvalidError(APIError):
    """令牌的会话编号缺失/为空/与当前有效会话不一致（issue #89）。

    三种成因（旧令牌不携带该声明、声明为空串、账号已在别处重新登录致使编号被覆盖）对
    用户而言是同一个可操作结论——"这张令牌不再代表当前有效会话，请重新登录"，故合并成
    一个错误码，不逐个细分。**必须**用这个 `APIError` 子类而非裸 `HTTPException`：
    后者会被 `http_exception_handler` 统一映射成 `auth.unauthorized`，前端就分不出
    "被顶号"与"令牌单纯过期/畸形"，做不出"账号已在其他浏览器登录"这句区别文案。
    """

    http_status = 401
    code = "auth.session_invalid"
    message = "登录状态已失效，可能是账号在其他浏览器登录，请重新登录"


class NotFoundError(APIError):
    """目标不存在。"""

    http_status = 404
    code = "resource.not_found"
    message = "目标不存在"


class ConflictError(APIError):
    """与当前状态冲突（重复、并发占用）。"""

    http_status = 409
    code = "resource.conflict"
    message = "与当前状态冲突"


class PayloadTooLargeError(APIError):
    """请求或结果超出规模上限。"""

    http_status = 413
    code = "request.too_large"
    message = "超出规模上限"


class UnprocessableError(APIError):
    """语义合法但业务不接受。"""

    http_status = 422
    code = "request.unprocessable"
    message = "请求无法被处理"


class ServiceUnavailableError(APIError):
    """基础设施暂时不可用（数据库连接失败/超时/池耗尽）。

    与业务侧 500 分开：客户端重试有意义，且告警策略需要能单独设定。
    """

    http_status = 503
    code = "service.unavailable"
    message = "服务暂时不可用，请稍后重试"


# ---------------- 错误码常量（<域>.<原因>）----------------
#
# 集中声明便于检索"这个码在哪抛的、前端在哪分支"。域前缀避免跨模块撞名。

# 请求与鉴权
CODE_REQUEST_INVALID: Final = "request.invalid"          # 参数校验失败
CODE_REQUEST_BAD: Final = "request.bad"
CODE_REQUEST_TOO_LARGE: Final = "request.too_large"
CODE_REQUEST_UNPROCESSABLE: Final = "request.unprocessable"
CODE_AUTH_UNAUTHORIZED: Final = "auth.unauthorized"
CODE_AUTH_FORBIDDEN: Final = "auth.forbidden"
CODE_AUTH_SESSION_INVALID: Final = "auth.session_invalid"  # 会话被顶替/缺失/不匹配

# 通用资源
CODE_RESOURCE_NOT_FOUND: Final = "resource.not_found"
CODE_RESOURCE_CONFLICT: Final = "resource.conflict"

# 分析查询
CODE_ANALYSIS_SCOPE_INVALID: Final = "analysis.scope_invalid"
CODE_ANALYSIS_EXPORT_TOO_LARGE: Final = "analysis.export_too_large"
CODE_ANALYSIS_BUSY: Final = "analysis.busy"                 # 并发上限排队超时（503）

# ASIN/品牌悬浮历史（issue #96）
CODE_ANALYSIS_TOPSLOT_NOT_FOUND: Final = "analysis.topslot_keyword_not_found"
CODE_ANALYSIS_TOPSLOT_SITE_MISMATCH: Final = "analysis.topslot_site_mismatch"
CODE_ANALYSIS_TOPSLOT_PAGE_SIZE_EXCEEDED: Final = "analysis.topslot_page_size_exceeded"

# 关键词工具（增长榜与词库共用——两屏的作用域解析是同一套站点/期次判定）
CODE_KEYWORD_SCOPE_INVALID: Final = "keyword.scope_invalid"

# 关键词排名趋势详情（市场分析与我的词库共用同一个只读端点）
CODE_KEYWORD_TREND_NOT_FOUND: Final = "keyword.trend_not_found"
CODE_KEYWORD_TREND_SITE_MISMATCH: Final = "keyword.trend_site_mismatch"

# 词库（关键词工具 · 我的词库）
CODE_LIBRARY_GROUP_NOT_FOUND: Final = "library.group_not_found"
CODE_LIBRARY_GROUP_DUPLICATE: Final = "library.group_duplicate"      # 同站点下重名
CODE_LIBRARY_LIMIT_EXCEEDED: Final = "library.limit_exceeded"        # 分组数/条目数超上限
CODE_LIBRARY_KEYWORD_MISMATCH: Final = "library.keyword_mismatch"    # 词不属于分组的站点

# 竞对监控（关注清单）
CODE_COMPETITOR_GROUP_NOT_FOUND: Final = "competitor.group_not_found"
CODE_COMPETITOR_GROUP_DUPLICATE: Final = "competitor.group_duplicate"      # 同用户下重名
CODE_COMPETITOR_LIMIT_EXCEEDED: Final = "competitor.limit_exceeded"        # 关注总数/单批解析数超上限
CODE_COMPETITOR_PREVIEW_NOT_FOUND: Final = "competitor.preview_not_found"  # token 不存在/不属于当前用户
CODE_COMPETITOR_PREVIEW_EXPIRED: Final = "competitor.preview_expired"
# 采集端上报：这两个码是给**插件作者**看的，不是给终端用户看的——它们要能指出"你的选择器
# 取错了元素"或"你复用了幂等键"。
CODE_COMPETITOR_SELLER_NOT_WATCHED: Final = "competitor.seller_not_watched"
CODE_COMPETITOR_IDEMPOTENCY_KEY_REUSED: Final = "competitor.idempotency_key_reused"
CODE_COMPETITOR_REPORT_EMPTY: Final = "competitor.report_empty"      # 零条有效 ASIN，禁止落定空基线
CODE_COMPETITOR_SITE_INVALID: Final = "competitor.site_invalid"

# 导入与对象存储
CODE_UPLOAD_IMPORT_REJECTED: Final = "upload.import_rejected"
CODE_UPLOAD_OSS_NOT_READY: Final = "upload.oss_not_ready"
CODE_UPLOAD_OBJECT_NOT_FOUND: Final = "upload.object_not_found"
CODE_UPLOAD_BAD_FILE: Final = "upload.bad_file"
CODE_UPLOAD_FILE_TOO_LARGE: Final = "upload.file_too_large"
CODE_UPLOAD_BATCH_NOT_FOUND: Final = "upload.batch_not_found"
CODE_UPLOAD_RETRY_NOT_ALLOWED: Final = "upload.retry_not_allowed"    # 状态不允许重导
CODE_UPLOAD_SOURCE_NOT_RETAINED: Final = "upload.source_not_retained"  # 原始文件未留存

# 用户管理
CODE_USER_DUPLICATE_NAME: Final = "user.duplicate_name"
CODE_USER_NOT_FOUND: Final = "user.not_found"
CODE_USER_SUPER_PROTECTED: Final = "user.super_protected"

# 钉钉单点登录
CODE_SSO_STATE_INVALID: Final = "sso.state_invalid"
CODE_SSO_CODE_INVALID: Final = "sso.code_invalid"
CODE_SSO_NOT_EMPLOYEE: Final = "sso.not_employee"
CODE_SSO_ACCOUNT_DISABLED: Final = "sso.account_disabled"
CODE_SSO_PROVIDER_UNAVAILABLE: Final = "sso.provider_unavailable"
CODE_SSO_DISABLED: Final = "sso.disabled"

# 基础设施与兜底
CODE_DB_UNAVAILABLE: Final = "db.unavailable"
CODE_DB_ERROR: Final = "db.error"
CODE_DB_QUERY_TIMEOUT: Final = "db.query_timeout"           # 交互查询超出执行预算（503）
CODE_INTERNAL_UNHANDLED: Final = "internal.unhandled"
