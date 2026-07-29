from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Boolean, ForeignKey, Integer, String, func
from datetime import datetime
from typing import Optional

from config import settings
from database import Base
from app.user.rbac_model import Role  # rbac_model 不反向依赖本模块，无循环导入


class UserCenter(Base):
    __tablename__ = "user_center"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_pwd: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # is_super 迁移期保留：amis 外壳仍读它；阶段七删 amis 时一并清理。授权以 role 为准。
    is_super: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 每用户一个角色（RBAC，见 rbac_model.py）。迁移回填后有值；DB 层留 nullable 以防插入遗漏。
    role_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(f"{settings.DATABASE_SCHEMA}.role.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now(), onupdate=func.now())

    # 角色随用户取出，供鉴权解析权限
    role: Mapped[Optional[Role]] = relationship(lazy="selectin")

    @property
    def permission_codes(self) -> set[str]:
        return self.role.permission_codes if self.role else set()

    @property
    def role_code(self) -> Optional[str]:
        return self.role.code if self.role else None
