"""用户域业务异常。

用户模块没有独立的 service 层（API 直接调 CRUD），异常因此定义在域内、**由 CRUD
层抛出**。补 service 层不在本变更范围内。

早先这些异常只在 API 层抛，CRUD 用 ``(success, message, user)`` 元组表达失败，
API 再按中文子串把它猜回状态码。现在失败一律从 CRUD 直接抛出：错误的语义
（404 还是 409、属于哪个错误码）在最清楚它的地方定，而不是压成字符串再猜。
"""

from __future__ import annotations

from app.common.errors import (
    CODE_USER_DUPLICATE_NAME,
    CODE_USER_NOT_FOUND,
    CODE_USER_SUPER_PROTECTED,
    ConflictError,
    NotFoundError,
)


class DuplicateUserNameError(ConflictError):
    """用户名已存在 → 409。

    409 而非 400：请求本身合法，是与当前状态（已有同名用户）冲突。
    """

    code = CODE_USER_DUPLICATE_NAME
    message = "用户名已存在"


class UserNotFoundError(NotFoundError):
    """目标用户不存在 → 404。"""

    code = CODE_USER_NOT_FOUND
    message = "用户不存在"


class SuperUserDeactivationError(ConflictError):
    """不能禁用超级管理员 → 409。

    409 而非 403：调用方权限没问题（能走到这里说明已过 user:manage），是这个操作
    与目标用户的当前状态冲突。禁掉最后的超管会把系统锁死，故直接拒绝。
    """

    code = CODE_USER_SUPER_PROTECTED
    message = "不能禁用超级管理员"
