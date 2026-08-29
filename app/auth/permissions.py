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
# 「关键词工具」菜单下两屏（持续增长市场、我的词库）共用的**容器级**权限点。
#
# 不给两屏各开一个 `growth:*` / `library:*`——我的词库管理的是用户自己的私有清单，
# 「禁止某人管理自己的收藏夹」不是会真实发生的授权需求，那样的权限点只会全员常开。
#
# 前身叫 `toolbox:view`，覆盖四屏。外观专利与全球商标库拆去「侵权查询」菜单后**不再由
# 任何权限点把守**（只要求已登录）：它们没有后端端点，罩着它们的权限点对它们不生效，
# 与被移除的 `user:manage` 同类。接上数据源、有了真实端点再新增，那时是加法。
#
# 注意它与 `user:manage` 不是一回事，别照那个先例把它也删掉：`user:manage` 被删是因为
# 后端根本不读它（勾了也不生效），而这个是逐路由真执行的，只是三个内置角色恰好都授予了。
# 保留它是为了留住「只看分析屏、不看关键词工具」这个开关的可能性——并进 `ANALYSIS_VIEW`
# 会把那个可能性一并删掉，删掉容易、加回来要再走一次迁移。
KEYWORD_VIEW: Final = "keyword:view"
# 竞对监控是独立数据域（自己的表、自己的用户隔离逻辑），不塞进 KEYWORD_VIEW——每个用户的
# 关注池互相独立，不是「全体共用一个开关」的授权粒度，与 ANALYSIS_VIEW 等同一档次。
COMPETITOR_VIEW: Final = "competitor:view"

# code -> 中文名
PERMISSIONS: Final[dict[str, str]] = {
    ANALYSIS_VIEW: "查询分析数据",
    ANALYSIS_EXPORT: "导出分析数据",
    UPLOAD_IMPORT: "上传/导入数据",
    KEYWORD_VIEW: "查看关键词工具",
    COMPETITOR_VIEW: "查看竞对监控",
}

# ---- 内置角色 ----  code -> (中文名, 权限集合)
ROLES: Final[dict[str, tuple[str, set[str]]]] = {
    "admin": ("超级管理员", set(PERMISSIONS.keys())),
    "operator": (
        "运营",
        {ANALYSIS_VIEW, ANALYSIS_EXPORT, UPLOAD_IMPORT, KEYWORD_VIEW, COMPETITOR_VIEW},
    ),
    "analyst": ("分析师", {ANALYSIS_VIEW, ANALYSIS_EXPORT, KEYWORD_VIEW, COMPETITOR_VIEW}),
}

# is_super 回填映射（见 design 决策 4b：保住老用户上传能力）
DEFAULT_ROLE_FOR_SUPER: Final = "admin"
DEFAULT_ROLE_FOR_NON_SUPER: Final = "operator"
