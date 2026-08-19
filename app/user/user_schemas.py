from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UserCenterBase(BaseModel):
    """用户登录模型"""
    user_name: str = Field(..., min_length=3, max_length=50, description="用户名")
    is_active: bool = Field(default=True, description="账号是否可用")
    is_super: bool = Field(default=False, description="是否为超级用户")


class UserCenterCreate(UserCenterBase):
    """用户注册模型"""
    password: str = Field(..., min_length=6, max_length=128, description="密码")
    role_id: int | None = Field(None, description="角色 ID；缺省按 is_super 赋默认角色")

    @field_validator('user_name')
    @classmethod
    def validate_username(cls, v):
        if not v.isalnum():
            raise ValueError('用户名只能包含字母和数字')
        return v.lower()

    @field_validator('password')
    @classmethod
    def validate_password(cls, v):
        if len(v) < 6:
            raise ValueError('密码长度至少6位')
        return v


class UserCenterUpdate(BaseModel):
    user_name: str | None = Field(None, min_length=3, max_length=50, description="用户名")
    password: str | None = Field(None, min_length=6, max_length=50, description="密码")
    is_active: bool | None = Field(None, description="是否激活")
    role_id: int | None = Field(None, description="角色 ID")

    @field_validator('user_name')
    @classmethod
    def validate_username(cls, v):
        if v and not v.isalnum():
            raise ValueError('用户名只能包含字母和数字')
        return v.lower() if v else v

    @field_validator('password')
    @classmethod
    def validate_password(cls, v):
        if v and len(v) < 6:
            raise ValueError('密码长度至少6位')
        return v


class UserCenter(UserCenterBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class UserCenterList(BaseModel):
    """用户列表响应模型"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_name: str
    # 展示名与来源：让管理员一眼分得清哪些账号是钉钉扫码自动建的、哪些是本地建的。
    # 二者的可管理性不同——本地账号能改密码，SSO 账号没有密码可改。
    display_name: str | None = None
    external_idp: str | None = None
    is_active: bool
    is_super: bool
    role_id: int | None = None
    role_code: str | None = None
    created_at: datetime
    updated_at: datetime


class RoleOut(BaseModel):
    """角色下拉项"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str


class UserCenterLogin(BaseModel):
    """用户登录模型"""
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")


class UserCenterLoginResponse(BaseModel):
    """登录响应模型"""
    access_token: str
    token_type: str = "bearer"
    user_info: UserCenter


class UserLoginResponse(BaseModel):
    """登录响应模型"""
    access_token: str = Field(..., description="访问令牌")
    token_type: str = Field(default="bearer", description="令牌类型")
    expires_in: int = Field(..., description="令牌过期时间(秒)")
    user: dict = Field(..., description="用户信息")
