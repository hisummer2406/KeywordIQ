"""RBAC 权限与内置角色的规范定义（运行时唯一事实来源）。

权限判定用这里的常量；Alembic 种子迁移按同一份矩阵硬编码写入 DB（迁移不 import 应用代码，
两处若调整需同步，见 design 决策 4b）。
"""
from typing import Final

# ---- 权限点 ----
ANALYSIS_VIEW: Final = "analysis:view"
ANALYSIS_EXPORT: Final = "analysis:export"
UPLOAD_IMPORT: Final = "upload:import"
USER_MANAGE: Final = "user:manage"
ROLE_MANAGE: Final = "role:manage"

# code -> 中文名
PERMISSIONS: Final[dict[str, str]] = {
    ANALYSIS_VIEW: "查询分析数据",
    ANALYSIS_EXPORT: "导出分析数据",
    UPLOAD_IMPORT: "上传/导入数据",
    USER_MANAGE: "用户管理",
    ROLE_MANAGE: "角色与权限管理",
}

# ---- 内置角色 ----  code -> (中文名, 权限集合)
ROLES: Final[dict[str, tuple[str, set[str]]]] = {
    "admin": ("超级管理员", set(PERMISSIONS.keys())),
    "operator": ("运营", {ANALYSIS_VIEW, ANALYSIS_EXPORT, UPLOAD_IMPORT}),
    "analyst": ("分析师", {ANALYSIS_VIEW, ANALYSIS_EXPORT}),
}

# is_super 回填映射（见 design 决策 4b：保住老用户上传能力）
DEFAULT_ROLE_FOR_SUPER: Final = "admin"
DEFAULT_ROLE_FOR_NON_SUPER: Final = "operator"
