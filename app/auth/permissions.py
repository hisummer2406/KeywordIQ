"""RBAC 权限与内置角色的规范定义（运行时唯一事实来源）。

权限判定用这里的常量；Alembic 种子迁移按同一份矩阵硬编码写入 DB（迁移不 import 应用代码，
两处若调整需同步，见 design 决策 4b）。

**这里只登记「可授予」的能力。** 用户管理不在其列——它要求 `is_super`，由
`simple_auth.require_super()` 把守，改角色配置不能把它授予出去。曾经有过一个
`user:manage` 权限点，收紧后它就成了「勾了也不生效」的复选框，比没有这个权限点更糟：
角色编辑界面会让人以为把它给了运营，运营就能管用户。同批移除的还有 `role:manage`
——它从加入起就没有任何代码读过，是纯粹的死配置。

**`admin` 角色默认拥有 `PERMISSIONS` 里的全部权限**（见下方 `ROLES["admin"]`），除了
登记在 `SUPER_ONLY_PERMISSIONS` 里的例外。这个例外是为 `opportunity:view` 引入的
（issue #76；该权限点已于 change add-opportunity-dossier 移出，集合目前为空，机制保留）：`role_code == 'admin'` 与 `is_super == True` 是两个独立字段（见
`user_crud.create_user` 的「显式指定角色优先，否则才按 `is_super` 赋默认角色」），
"仅超管可见"指的是后者。若不把新权限点放进 `SUPER_ONLY_PERMISSIONS`，它会被
`ROLES["admin"]` 的"全部权限"定义自动吸收——一个 `admin` 角色但 `is_super=False`
的用户也会看到，这与"仅超管可见"的要求相反。默认（不放进这个集合）仍是"三个内置
角色按各自既有矩阵获得，admin 隐含全部"，这是常态；放进去是例外，加了就要在权限点
自己的注释里写明为什么。
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
# 前身叫 `toolbox:view`，覆盖四屏。外观专利与全球商标库拆去「风险核查」菜单（issue #76
# 前叫「侵权查询」）后**不再由任何权限点把守**（只要求已登录）：它们没有后端端点，
# 罩着它们的权限点对它们不生效，与被移除的 `user:manage` 同类。接上数据源、有了真实
# 端点再新增，那时是加法。
#
# 注意它与 `user:manage` 不是一回事，别照那个先例把它也删掉：`user:manage` 被删是因为
# 后端根本不读它（勾了也不生效），而这个是逐路由真执行的，只是三个内置角色恰好都授予了。
# 保留它是为了留住「只看分析屏、不看关键词工具」这个开关的可能性——并进 `ANALYSIS_VIEW`
# 会把那个可能性一并删掉，删掉容易、加回来要再走一次迁移。
KEYWORD_VIEW: Final = "keyword:view"
# 竞对监控是独立数据域（自己的表、自己的用户隔离逻辑），不塞进 KEYWORD_VIEW——每个用户的
# 关注池互相独立，不是「全体共用一个开关」的授权粒度，与 ANALYSIS_VIEW 等同一档次。
COMPETITOR_VIEW: Final = "competitor:view"
# 机会中心（issue #76）。上线初期仅超管可见；change add-opportunity-dossier（2026-11-12 用户决定
# 推进到运营可用）起授予三个内置角色。它把守机会档案与 IP 专家的全部端点；IP 专家里会向智慧芽
# 计费的动作另外要求 RISK_SEARCH（见下），不随这个权限点铺开。
OPPORTUNITY_VIEW: Final = "opportunity:view"
# 定价核算（change add-pricing-calculation）。核价记录按创建人私有，费用规则是全员共享的。
# 「能核价」与「能改共享费率」是两种授权：费率写错会让所有人的核价一起算错，所以单独
# 一个权限点，只默认给 admin。读取费率不需要 manage 权限——核价明细本来就要展示所用规则。
PRICING_VIEW: Final = "pricing:view"
PRICING_MANAGE_RATES: Final = "pricing:manage-rates"
# 外观专利核查（change add-patent-risk-check，#24）。三种授权分开：看自己的记录、发起会计费的
# 智慧芽调用、签署人工意见。只默认给 admin（admin 隐含全部非 super-only 权限），运营/分析师由
# 超管显式授予——检索按次计费，不能随角色默认铺开。记录按创建人私有，与权限点无关。
RISK_VIEW: Final = "risk:view"
RISK_SEARCH: Final = "risk:search"
RISK_REVIEW: Final = "risk:review"

# code -> 中文名
PERMISSIONS: Final[dict[str, str]] = {
    ANALYSIS_VIEW: "查询分析数据",
    ANALYSIS_EXPORT: "导出分析数据",
    UPLOAD_IMPORT: "上传/导入数据",
    KEYWORD_VIEW: "查看关键词工具",
    COMPETITOR_VIEW: "查看竞对监控",
    OPPORTUNITY_VIEW: "查看机会中心",
    PRICING_VIEW: "定价核算",
    PRICING_MANAGE_RATES: "维护核价费用配置",
    RISK_VIEW: "查看风险核查",
    RISK_SEARCH: "发起专利检索（计费）",
    RISK_REVIEW: "提交风险审核意见",
}

# 默认不授予任何内置角色（含 admin）的权限点——只有 is_super 能用。
# 见文件头部 docstring："admin = 全部权限" 这条默认规则在这里被显式挖掉例外。
# 当前为空：`opportunity:view` 已于 change add-opportunity-dossier 授予内置角色。机制保留，
# 以后「先仅超管试用」的权限点仍登记在这里。
SUPER_ONLY_PERMISSIONS: Final[frozenset[str]] = frozenset()

# ---- 内置角色 ----  code -> (中文名, 权限集合)
ROLES: Final[dict[str, tuple[str, set[str]]]] = {
    "admin": ("超级管理员", set(PERMISSIONS.keys()) - SUPER_ONLY_PERMISSIONS),
    "operator": (
        "运营",
        {ANALYSIS_VIEW, ANALYSIS_EXPORT, UPLOAD_IMPORT, KEYWORD_VIEW, COMPETITOR_VIEW,
         PRICING_VIEW, OPPORTUNITY_VIEW},
    ),
    "analyst": (
        "分析师",
        {ANALYSIS_VIEW, ANALYSIS_EXPORT, KEYWORD_VIEW, COMPETITOR_VIEW, PRICING_VIEW, OPPORTUNITY_VIEW},
    ),
}

# is_super 回填映射（见 design 决策 4b：保住老用户上传能力）
DEFAULT_ROLE_FOR_SUPER: Final = "admin"
DEFAULT_ROLE_FOR_NON_SUPER: Final = "operator"
