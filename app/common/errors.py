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

# 通用资源
CODE_RESOURCE_NOT_FOUND: Final = "resource.not_found"
CODE_RESOURCE_CONFLICT: Final = "resource.conflict"

# 分析查询
CODE_ANALYSIS_SCOPE_INVALID: Final = "analysis.scope_invalid"
CODE_ANALYSIS_EXPORT_TOO_LARGE: Final = "analysis.export_too_large"

# 工具箱
CODE_TOOLBOX_SCOPE_INVALID: Final = "toolbox.scope_invalid"
CODE_TOOLBOX_SORT_INVALID: Final = "toolbox.sort_invalid"

# 词库（工具箱 · 我的词库）
CODE_LIBRARY_GROUP_NOT_FOUND: Final = "library.group_not_found"
CODE_LIBRARY_GROUP_DUPLICATE: Final = "library.group_duplicate"      # 同站点下重名
CODE_LIBRARY_LIMIT_EXCEEDED: Final = "library.limit_exceeded"        # 分组数/条目数超上限
CODE_LIBRARY_KEYWORD_MISMATCH: Final = "library.keyword_mismatch"    # 词不属于分组的站点

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

# 基础设施与兜底
CODE_DB_UNAVAILABLE: Final = "db.unavailable"
CODE_DB_ERROR: Final = "db.error"
CODE_INTERNAL_UNHANDLED: Final = "internal.unhandled"
