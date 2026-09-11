# app/auth/auth_api.py
"""认证 API（干净 REST，供 SPA）：POST /api/auth/token、GET /api/auth/me、POST /api/auth/logout。

amis 时期的信封端点（/login、/profile）已随 amis 于阶段七删除。
"""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import sso_service
from app.auth.dingtalk_client import DingTalkClient
from app.auth.simple_auth import serialize_user, simple_auth
from app.auth.sso_errors import SsoDisabledError
from app.user.user_crud import UserCenterCRUD
from app.user.user_model import UserCenter
from config import settings
from database import get_async_db

auth_router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class SsoCallbackRequest(BaseModel):
    auth_code: str
    state: str


# 企业 accessToken 的缓存挂在实例上，因此这里要一个**进程内长活**的客户端，
# 不能每请求新建——每请求新建等于缓存永远为空，每次登录都去换一次企业凭证，
# 钉钉侧会因频繁换取而限流，表现为随机的登录失败。
_dingtalk_client: DingTalkClient | None = None


def get_dingtalk_client() -> DingTalkClient:
    global _dingtalk_client
    if _dingtalk_client is None:
        _dingtalk_client = DingTalkClient(
            app_key=settings.DINGTALK_APP_KEY,
            app_secret=settings.DINGTALK_APP_SECRET,
        )
    return _dingtalk_client


def _require_sso_enabled() -> None:
    if not settings.SSO_ENABLED:
        raise SsoDisabledError(log_detail="SSO_ENABLED=false，端点被调用")


# ---------------- 干净 REST（SPA） ----------------

@auth_router.post("/token")
async def issue_token(login_data: LoginRequest):
    """登录并签发令牌。返回 {token, user}。"""
    result = await simple_auth.login(login_data.username, login_data.password)
    return {"token": result["access_token"], "user": result["user"]}


@auth_router.get("/me")
async def get_me(current_user: UserCenter = Depends(simple_auth.get_current_user)):
    """当前登录用户（含角色与权限码）。"""
    return serialize_user(current_user)


@auth_router.post("/logout")
async def logout(
    request: Request,
    current_user: UserCenter = Depends(simple_auth.get_current_user_for_logout),
    db: AsyncSession = Depends(get_async_db),
):
    """登出：条件清除当前会话编号（issue #89），使这张令牌立即失效。

    鉴权依赖是 `get_current_user_for_logout` 而不是 `get_current_user`——后者会在
    会话已经不匹配时提前 401，永远走不到下面这条条件更新（PR #91 复审第六轮 P2，
    详见该依赖的说明）。条件是"仍等于本次令牌携带的会话编号"——若账号已在别处重新
    登录、`active_session_id` 已被覆盖，这次清除天然是空操作，不会误删新会话，也不
    当作错误处理（见 `UserCenterCRUD.clear_session_if_current`）。`session_id` 取自
    该依赖挂在 `request.state` 上的值，不重复解码一次令牌；这张令牌本就没有
    session_id（本功能上线前签发的旧令牌）时跳过这次数据库调用——没有基线可比对，
    执行一次必然 0 行命中的 UPDATE 只会白占一次写入。
    """
    session_id = request.state.session_id
    if session_id:
        crud = UserCenterCRUD(db)
        await crud.clear_session_if_current(current_user.id, session_id)
    return {"success": True}


# ---------------- 钉钉单点登录 ----------------
#
# 三个端点**全部免鉴权**——登录页此时还没有令牌，挂上鉴权依赖这些入口永远够不着。
# 它们不读任何用户数据，只做「发起授权」与「用授权码换令牌」两件事。

@auth_router.get("/sso/status")
async def sso_status():
    """SSO 是否可用，供登录页决定要不要渲染钉钉按钮。

    存在的理由是让「关闭时不显示按钮」有个正经判据。没有它，前端只能靠「请求授权地址
    拿到失败响应」来推断，于是每次进登录页都要发一个注定失败的请求。
    """
    return {"enabled": settings.SSO_ENABLED, "provider_name": "钉钉"}


@auth_router.get("/sso/authorize-url")
async def sso_authorize_url():
    """钉钉扫码授权页地址。

    **不接受任何请求参数。** `redirect_uri` 一律取自配置——可由请求方传入即是开放
    重定向，攻击者能把授权码引到自己的域名上。
    """
    _require_sso_enabled()
    return {"url": sso_service.build_authorize_url()}


@auth_router.post("/sso/callback")
async def sso_callback(
    payload: SsoCallbackRequest,
    db: AsyncSession = Depends(get_async_db),
    client: DingTalkClient = Depends(get_dingtalk_client),
):
    """用授权码换本系统令牌。返回 `{token, user}`，与 `POST /token` 完全同形。

    同形不是巧合：前端因此能复用登录页那套令牌落地与跳转逻辑，令牌也始终走普通 JSON
    响应，不进 URL、不进浏览器历史。
    """
    _require_sso_enabled()
    result = await sso_service.login_with_auth_code(
        db, client, auth_code=payload.auth_code, state=payload.state
    )
    return {"token": result["token"], "user": serialize_user(result["user"])}
