# app/auth/auth_api.py
"""认证 API（干净 REST，供 SPA）：POST /api/auth/token、GET /api/auth/me、POST /api/auth/logout。

amis 时期的信封端点（/login、/profile）已随 amis 于阶段七删除。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.simple_auth import serialize_user, simple_auth
from app.user.user_model import UserCenter

auth_router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


# ---------------- 干净 REST（SPA） ----------------

@auth_router.post("/token")
async def issue_token(login_data: LoginRequest):
    """登录并签发令牌。返回 {token, user}。"""
    result = await simple_auth.login(login_data.username, login_data.password)
    return {"token": result["access_token"], "user": result["user"]}


@auth_router.get("/me")
def get_me(current_user: UserCenter = Depends(simple_auth.get_current_user)):
    """当前登录用户（含角色与权限码）。"""
    return serialize_user(current_user)


@auth_router.post("/logout")
def logout(current_user: UserCenter = Depends(simple_auth.get_current_user)):
    """登出（JWT 无状态，前端清 token 即可；此处仅确认令牌有效）。"""
    return {"success": True}
