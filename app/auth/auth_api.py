# app/auth/auth_api.py
"""认证 API。

两套并存（迁移期）：
- 干净 REST（SPA 用）：POST /api/auth/token、GET /api/auth/me、POST /api/auth/logout
- amis 信封（旧 amis UI 用，阶段七随 amis 删除）：POST /api/auth/login、GET /api/auth/profile
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.simple_auth import simple_auth, serialize_user
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


# ---------------- amis 信封（旧 UI，迁移期兼容） ----------------

@auth_router.post("/login")
async def login(login_data: LoginRequest):
    """[amis 兼容] 用户登录"""
    try:
        result = await simple_auth.login(login_data.username, login_data.password)
        return {"status": 0, "msg": "登录成功", "data": result}
    except HTTPException as e:
        return {"status": 1, "msg": e.detail, "data": None}


@auth_router.get("/profile")
def get_profile(current_user: UserCenter = Depends(simple_auth.get_current_user)):
    """[amis 兼容] 获取当前用户信息"""
    return {
        "status": 0,
        "msg": "获取成功",
        "data": {
            "id": current_user.id,
            "username": current_user.user_name,
            "is_super": current_user.is_super,
            "is_active": current_user.is_active,
            "role": current_user.role_code,
            "created_at": current_user.created_at.isoformat()
        }
    }
