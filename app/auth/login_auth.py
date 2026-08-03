from datetime import datetime, timedelta
from typing import Any

import jwt

from config import settings


class AuthService:
    """认证服务"""

    @staticmethod
    def create_access_token(
        user_id: int, username: str, is_super: bool, role: str | None = None
    ) -> str:
        """生成访问令牌。

        令牌承载 role（授权用）与 is_super（迁移期兼容 amis）。权限码不入令牌——避免
        admin 改角色权限后令牌陈旧，权限在请求时由用户所属角色实时解析。
        """
        payload = {
            "user_id": user_id,
            "username": username,
            "is_super": is_super,
            "role": role,
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
