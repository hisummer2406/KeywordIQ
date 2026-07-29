"""RBAC 模型：角色、权限、角色-权限映射。

设计见 openspec/changes/migrate-frontend-to-antd-pro/design.md 决策 4/4b：
- 每用户一个角色（`user_center.role_id` FK，见 user_model.py）；
- 角色↔权限为多对多（`role_permission`），admin 可配角色的权限集；
- 权限点 5 个、内置角色 3 个（admin/operator/analyst），种子数据由 Alembic 迁移写入。
全部落在 `settings.DATABASE_SCHEMA`（analysis）下，随 Base.metadata。
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Table, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from config import settings
from database import Base

_SCHEMA = settings.DATABASE_SCHEMA

# 角色-权限多对多关联表
role_permission = Table(
    "role_permission",
    Base.metadata,
    Column(
        "role_id",
        ForeignKey(f"{_SCHEMA}.role.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "permission_id",
        ForeignKey(f"{_SCHEMA}.permission.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Permission(Base):
    __tablename__ = "permission"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="如 analysis:view")
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class Role(Base):
    __tablename__ = "role"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, comment="如 admin/operator/analyst")
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="内置角色，禁止删除"
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # 权限随角色一并取出（角色数量少、每请求要判权限，selectin 一次拉全）
    permissions: Mapped[list["Permission"]] = relationship(
        secondary=role_permission, lazy="selectin"
    )

    @property
    def permission_codes(self) -> set[str]:
        return {p.code for p in self.permissions}
