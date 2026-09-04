# 导航演进：为主动式机会工作流预置菜单与权限边界

Refs #76

## Why

产品后续会加「确定性 Signal → 候选机会 → 验证推进 → 风险核查」这条主动式工作流
（issue #7 / #13 / #24 / #25 / #38 / #54 / #55 各承接一段）。如果现在不预留入口，
这些能力做出来的时候只能面对两个坏选择：塞进现有屏（用户在"发现热点词"之后找不到
"推进和验证机会"的主入口），或者以"AI Agent"命名新入口（把技术实现误当成用户任务，
且与"不让 Agent 自动执行调价/上下架/采购"这条产品边界冲突——Agent 是实现手段，
不是产品叫法）。

本变更只做导航与权限的骨架，不实现任何机会中心的业务逻辑（信号计算、机会评分、
验证流程都在依赖的 issue 里）：新增一级菜单「机会中心」与其空状态页、新增权限点
`opportunity:view`（默认不授予任何内置角色，只有 `is_super` 能看到）、把另外两个
菜单按数据域重新命名以配合六项顺序。

## What Changes

**顶栏从五项变六项，重新排序**：

```
机会中心      /opportunity （单页，全宽，新增）         opportunity:view（仅超管）
关键词市场分析 /analysis    （单页，全宽，改名自"ABA数据选品"） analysis:view 不变
关键词工具    /keyword     ├ 持续增长市场 ├ 我的词库    keyword:view 不变
竞对监控      /competitor  （单页）                    competitor:view 不变
风险核查      /ip          ├ 外观专利 ├ 全球商标库      不设权限点（改名自"侵权查询"）
数据管理      /upload      ├ 上传文件 ├ 导入历史        upload:import 不变（改名自"数据上传"）
```

- **新增「机会中心」**：单页顶层（不设二级菜单，遵循"单页顶层不长侧栏"的既有约束），
  内容是按 issue 建议的六个状态（待处理信号/候选机会/验证中/风险待核查/已通过/
  已否决）分 Tab 的空状态说明，**不发任何请求**——没有信号计算、没有机会表，此刻
  发请求等于伪造一个查不出东西的端点。
- **新增权限点 `opportunity:view`**，写入 `permissions.py` 的 `PERMISSIONS` 字典，
  **不加入任何内置角色的授权集合**——`admin`/`operator`/`analyst` 三个角色都不获得它，
  只有 `is_super` 用户能看到这个菜单（`require_permission` 对 `is_super` 的兜底）。
  这与 `competitor:view` 那次"新权限点授予三个内置角色"的先例不同，是 issue 明确要求
  的"仅超管可见"。
- **`ABA数据选品` 改名 `关键词市场分析`**：路由 `/analysis`、组件、`analysis:view`
  权限点均不变，只改菜单显示的中文名与相关注释里的称呼。
- **`侵权查询` 改名 `风险核查`**：路由 `/ip`、两个子屏、无权限点的既有形态都不变，
  只改菜单名。**不新增权限点**——两个子屏依然没有后端端点，issue 里"可保持仅超管可见"
  是给了这个选项而非强制，本仓库已有的先例（`user:manage`、占位屏权限点）明确反对
  为没有端点的屏挂权限点，那是"勾了也不生效"的复选框；等接入真实数据源、有了端点，
  加权限点是一次干净的加法。
- **`数据上传` 改名 `数据管理`**：路由 `/upload`、两个子屏、`upload:import` 权限点
  都不变，只改菜单名，为后续 SP-API/第三方连接器接入时的语义扩展预留名字。
- **顶栏排序**：机会中心排最前——它是新的主动式工作流入口，issue 明确把它放在首位；
  其余五项相对顺序不变。

## Impact

- Affected specs: `top-nav-structure`（MODIFIED：六项、三处改名、排序、`风险核查` 不设
  权限点的显式重申），`opportunity-center`（ADDED：新能力，机会中心的空状态与权限边界）
- Affected code:
  - `frontend/src/routes.ts`（菜单结构、新增 `/opportunity` 路由）
  - `frontend/src/access.ts`（新增 `canViewOpportunity`）
  - `frontend/src/pages/Opportunity/`（新增，空状态页）
  - `frontend/src/routes.test.ts`（顶栏结构回归更新）
  - `app/auth/permissions.py`（新增 `OPPORTUNITY_VIEW`，不授予任何内置角色）
  - 一条新 Alembic 迁移（写入 `permission` 表，不写 `role_permission`）
  - `test/test_rbac_auth_unit.py` 等权限矩阵回归
  - `CLAUDE.md`「顶栏菜单」一节：补充机会中心的归属判据与权限策略说明
