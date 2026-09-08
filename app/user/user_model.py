from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.user.rbac_model import Role  # rbac_model 不反向依赖本模块，无循环导入
from config import settings
from database import Base


class UserCenter(Base):
    __tablename__ = "user_center"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    # 可空：SSO 账号没有密码。这是**事实**而非哨兵值——塞一个"永不匹配"的散列，其正确性
    # 依赖「bcrypt 不会碰巧匹配」这条隐含约定，NULL 不需要任何约定。
    # 拒绝 SSO 账号走密码登录由 user_crud.authenticate_user 既有的哑哈希路径承担，
    # 不要为此新增提前返回的分支（那会重开「用响应耗时判断某用户名是不是 SSO 账号」的侧信道）。
    hashed_pwd: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # is_super 迁移期保留：amis 外壳仍读它；阶段七删 amis 时一并清理。授权以 role 为准。
    is_super: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 显示名。`user_name` 同时承担登录名与显示名两个职责，SSO 之后必须拆开——
    # 钉钉侧的登录标识是英文工号，界面上全是 zhangsan 这种字样实际很难用。
    display_name: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="展示用名称；SSO 账号取自身份源的姓名"
    )
    # 头像地址。**与 display_name 刻意不同**：display_name 已建档账号不回写（回写要处理
    # 用户名唯一约束的连锁影响），头像没有唯一约束顾虑，故每次 SSO 登录都刷新——头像换了
    # 却在系统里显示旧照片，比显示名滞后更容易让人觉得"认错人"。本地账号恒为 NULL，
    # 前端回落到默认头像图标。
    avatar_url: Mapped[str | None] = mapped_column(
        String(512), nullable=True, comment="头像地址；SSO 账号每次登录同步身份源头像"
    )
    # 外部身份源标识。NULL 表示本地账号（可用密码登录），'dingtalk' 表示钉钉扫码建立。
    external_idp: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="外部身份源；NULL 表示本地账号"
    )
    # 该身份源内的**稳定**用户标识。钉钉存 unionId，不存企业内 userid——后者管理员可改、
    # 离职再入职会换，变一次即老账号成孤儿、当事人被当新人重新开号、角色全部丢失。
    external_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="该身份源内的稳定用户标识"
    )
    # 每用户一个角色（RBAC，见 rbac_model.py）。迁移回填后有值；DB 层留 nullable 以防插入遗漏。
    role_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{settings.DATABASE_SCHEMA}.role.id", ondelete="RESTRICT"), nullable=True
    )
    # 必须显式写 timezone=True。裸 Mapped[datetime] 推出的是**无时区**的 timestamp，
    # 而库里这两列是 timestamptz（迁移建的）——模型与库不一致时 autogenerate 会生成一条
    # 把列改窄的 alter_column，跑下去就把时区信息丢了。全仓其余时间戳列都带 timezone=True。
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        # 账号只按这一对匹配，绝不按用户名自动绑定已有账号——否则任何能改自己钉钉
        # userid 的人都可以把它改成 admin，扫一次码就接管超级管理员。
        #
        # PostgreSQL 默认 NULLS DISTINCT，本地账号两列均为 NULL 因而互不冲突，
        # 正是所需行为。不要改成 NULLS NOT DISTINCT。
        UniqueConstraint("external_idp", "external_id", name="uq_user_center_external"),
        {"comment": "用户表：登录账号与口令散列。"},
    )

    # 角色随用户取出，供鉴权解析权限
    role: Mapped[Role | None] = relationship(lazy="selectin")

    @property
    def permission_codes(self) -> set[str]:
        return self.role.permission_codes if self.role else set()

    @property
    def role_code(self) -> str | None:
        return self.role.code if self.role else None

    @property
    def role_name(self) -> str | None:
        """角色的展示名（如「超级管理员」）。

        透出它是为了让前端不必维护一份 code → 中文名的映射：那份映射会随角色增删
        静默漂移，而这里的名字与 role 表同源。
        """
        return self.role.name if self.role else None
