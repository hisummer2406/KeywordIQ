"""单点登录域的业务异常。

与 `user_errors` 同一形态：错误的语义（哪个状态码、哪个错误码）在最清楚它的地方定，
由服务层**抛出**，API 层不做 try/except 转译——路由层转译会把域内错误码盖成通用码。

**内部细节一律走 `log_detail`。** 钉钉返回的 errcode/errmsg、unionId、AppKey、
重定向 URL 这些排查时有用但不该给客户端看的信息只进日志，`message` 面向用户、可直接展示。
"""

from __future__ import annotations

from app.common.errors import (
    CODE_SSO_ACCOUNT_DISABLED,
    CODE_SSO_CODE_INVALID,
    CODE_SSO_DISABLED,
    CODE_SSO_NOT_EMPLOYEE,
    CODE_SSO_PROVIDER_UNAVAILABLE,
    CODE_SSO_STATE_INVALID,
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
)


class SsoStateInvalidError(BadRequestError):
    """state 过期、签名不符或缺失 → 400。

    400 而非 401：这不是「凭证无效」，而是这次登录握手本身已经不成立了，
    用户要做的是重新发起，不是换个凭证再试。
    """

    code = CODE_SSO_STATE_INVALID
    message = "登录已超时，请重新发起"


class SsoCodeInvalidError(BadRequestError):
    """授权码已被使用或已过期 → 400。

    钉钉的 authCode 一次性。刷新回调页、或用浏览器后退再前进都会重放同一个 code，
    这是用户最容易撞上的一种失败，文案要指向「重新登录」而不是「联系管理员」。
    """

    code = CODE_SSO_CODE_INVALID
    message = "授权码已失效，请重新登录"


class SsoNotEmployeeError(ForbiddenError):
    """unionId 不在本企业通讯录内 → 403。

    离职同步就落在这里：钉钉侧把人移出通讯录后，反查拿不到 userid，登录随即失败。
    外部人员（非本企业钉钉用户）扫码也走这条路径。
    """

    code = CODE_SSO_NOT_EMPLOYEE
    message = "你的钉钉账号不在本企业通讯录内，请联系管理员"


class SsoAccountDisabledError(ForbiddenError):
    """账号在本系统内被停用 → 403。

    与上一条分开：那条是「钉钉侧没有你」，这条是「钉钉侧有你，但本系统的管理员停用了你」。
    合成一个错误码会让管理员无从判断该去哪边查。
    """

    code = CODE_SSO_ACCOUNT_DISABLED
    message = "账号已被停用，请联系管理员"


class SsoProviderUnavailableError(ServiceUnavailableError):
    """钉钉服务不可达、超时或返回了系统级错误 → 503。

    **503 而非 500**：这是基础设施不可用，与数据库连接超时同类，需要与「我们的代码炸了」
    分别设定告警策略。文案要提到密码登录——那是钉钉挂掉时唯一的逃生通道。
    """

    code = CODE_SSO_PROVIDER_UNAVAILABLE
    message = "钉钉服务暂时不可用，请稍后重试或改用密码登录"


class SsoDisabledError(NotFoundError):
    """本部署没有开启 SSO，相关端点不可用 → 404。

    404 而非 403：不是"你没权限用"，而是这个部署根本没有这条入口。正常前端不会走到
    这里——登录页先问过 `/sso/status`，关闭时按钮就不渲染。
    """

    code = CODE_SSO_DISABLED
    message = "本系统未开启单点登录"
