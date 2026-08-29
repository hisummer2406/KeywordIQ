"""RBAC 权限与内置角色的规范定义（运行时唯一事实来源）。

权限判定用这里的常量；Alembic 种子迁移按同一份矩阵硬编码写入 DB（迁移不 import 应用代码，
两处若调整需同步，见 design 决策 4b）。

**这里只登记「可授予」的能力。** 用户管理不在其列——它要求 `is_super`，由
`simple_auth.require_super()` 把守，改角色配置不能把它授予出去。曾经有过一个
`user:manage` 权限点，收紧后它就成了「勾了也不生效」的复选框，比没有这个权限点更糟：
角色编辑界面会让人以为把它给了运营，运营就能管用户。同批移除的还有 `role:manage`
——它从加入起就没有任何代码读过，是纯粹的死配置。
"""
from typing import Final

# ---- 权限点 ----
ANALYSIS_VIEW: Final = "analysis:view"
ANALYSIS_EXPORT: Final = "analysis:export"
UPLOAD_IMPORT: Final = "upload:import"
# 工具箱下**全部**屏幕共用这一个权限点。不给外观专利、全球商标库各开一个——它们尚无
# 数据源、也没有端点可挂，那样的权限点就是「勾了也不生效」的复选框，与被移除的
# `user:manage` 同类。等它们真有端点时再拆，届时拆分是加法，不会留下失效的历史勾选。
TOOLBOX_VIEW: Final = "toolbox:view"
# 竞对监控是独立数据域（自己的表、自己的用户隔离逻辑），不塞进 TOOLBOX_VIEW——每个用户的
# 关注池互相独立，不是「全体共用一个开关」的授权粒度，与 ANALYSIS_VIEW 等同一档次。
COMPETITOR_VIEW: Final = "competitor:view"

# code -> 中文名
PERMISSIONS: Final[dict[str, str]] = {
    ANALYSIS_VIEW: "查询分析数据",
    ANALYSIS_EXPORT: "导出分析数据",
    UPLOAD_IMPORT: "上传/导入数据",
    TOOLBOX_VIEW: "查看工具箱",
    COMPETITOR_VIEW: "查看竞对监控",
}

# ---- 内置角色 ----  code -> (中文名, 权限集合)
ROLES: Final[dict[str, tuple[str, set[str]]]] = {
    "admin": ("超级管理员", set(PERMISSIONS.keys())),
    "operator": (
        "运营",
        {ANALYSIS_VIEW, ANALYSIS_EXPORT, UPLOAD_IMPORT, TOOLBOX_VIEW, COMPETITOR_VIEW},
    ),
    "analyst": ("分析师", {ANALYSIS_VIEW, ANALYSIS_EXPORT, TOOLBOX_VIEW, COMPETITOR_VIEW}),
}

# is_super 回填映射（见 design 决策 4b：保住老用户上传能力）
DEFAULT_ROLE_FOR_SUPER: Final = "admin"
DEFAULT_ROLE_FOR_NON_SUPER: Final = "operator"
