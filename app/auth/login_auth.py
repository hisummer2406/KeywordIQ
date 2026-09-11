from datetime import datetime, timedelta
from typing import Any

import jwt

from config import settings


class AuthService:
    """认证服务"""

    @staticmethod
    def create_access_token(
        user_id: int, username: str, is_super: bool, role: str | None, session_id: str
    ) -> str:
        """生成访问令牌。

        令牌承载 role（授权用）与 is_super（迁移期兼容 amis）。权限码不入令牌——避免
        admin 改角色权限后令牌陈旧，权限在请求时由用户所属角色实时解析。

        `session_id`（issue #89）由调用方在 `UserCenterCRUD.rotate_active_session` 落库
        之后传入——本方法是**纯签发者**，不自己生成会话编号，只负责把它编码进 JWT。
        必填而非可选：上线后不兼容缺少这个声明的旧令牌，让它成为可选参数会悄悄开一条
        "忘了传也能签出令牌"的路，而那张令牌会在 `get_current_user` 里被判定为会话缺失。
        """
        payload = {
            "user_id": user_id,
            "username": username,
            "is_super": is_super,
            "role": role,
            "session_id": session_id,
            "exp": datetime.utcnow() + timedelta(hours=24),
            "iat": datetime.utcnow()
        }
        return jwt.encode(payload, settings.ADMIN_SECRET_KEY, algorithm="HS256")

    @staticmethod
    def decode_access_token(token: str) -> dict[str, Any] | None:
        """解码访问令牌"""
        try:
            payload = jwt.decode(token, settings.ADMIN_SECRET_KEY, algorithms=["HS256"])
            return payload
        except jwt.ExpiredSignatureError:
            return None
        except jwt.PyJWTError:
            return None


# 创建认证服务实例
auth_service = AuthService()
