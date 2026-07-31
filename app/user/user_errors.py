"""用户域业务异常。

用户模块没有独立的 service 层（API 直接调 CRUD），异常因此定义在域内、由 API 层抛出。
补 service 层不在本变更范围内。
"""

from __future__ import annotations

from app.common.errors import (
    CODE_USER_DUPLICATE_NAME,
    CODE_USER_NOT_FOUND,
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
