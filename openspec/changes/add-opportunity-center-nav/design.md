# Design：机会中心导航骨架

## 决策 1：`opportunity:view` 不授予任何内置角色，只靠 `is_super` 兜底

`require_permission` 的判据是 `current_user.is_super or permission in current_user.permission_codes`
（`app/auth/simple_auth.py`）。issue #76 明确写"默认只授予超管，其他内置角色不自动获得"，
这意味着这次**不**像 `competitor:view` 那样把新权限点塞进 `admin`/`operator`/`analyst`
的授权集合——即便是 `admin` 角色的普通用户（`is_super=false`）也看不见机会中心，
只有 `is_super=true` 的用户能看到。

迁移里因此不写 `role_permission`：只 `INSERT INTO permission`，不做角色授予那一段
INSERT（参照 `a32f4b9a7841` 的写法，但 `_GRANTED_TO` 是空集合时 `WHERE r.code IN ()`
是非法 SQL，所以整段 role_permission 的 INSERT 直接省略，不是传空元组）。

**这里有一个陷阱，写代码前必须先读 `permissions.py` 现状才会发现**：`ROLES["admin"]`
一直写的是 `set(PERMISSIONS.keys())`——admin 角色的授权集合是动态算出来的"全部权限"，
不是逐条枚举。如果只把 `OPPORTUNITY_VIEW` 加进 `PERMISSIONS` 字典而不做任何处理，
`ROLES["admin"]` 会自动把它吸收进去；而 `role_code == 'admin'` 与 `is_super == True`
是两个独立字段（`user_crud.create_user` 允许显式指定角色，不强制跟随 `is_super`），
所以"admin 角色自动获得"与"仅超管可见"是两个不同的、会冲突的结论。`test_rbac_auth_unit.py`
里 `test_role_permission_matrix_matches_spec` 那条 `P.ROLES["admin"][1] ==
set(P.PERMISSIONS.keys())` 断言就是这条隐含规则的证据。

处理办法是显式挖一个例外，而不是把 `admin` 的定义从"动态算全部"改成"手动枚举"（后者
会让下一个正常权限点也要求手动加进 `admin` 的集合，丢失"新权限点默认对 admin 生效"
这条本来省心的默认值）：新增 `SUPER_ONLY_PERMISSIONS: frozenset[str]`，`ROLES["admin"]`
改成 `set(PERMISSIONS.keys()) - SUPER_ONLY_PERMISSIONS`。`OPPORTUNITY_VIEW` 放进这个
集合，其余权限点不受影响。`test_role_permission_matrix_matches_spec` 的断言需要同步改写
（不再是"等于全部权限"，而是"等于全部权限减去 super-only 集合"），并新增一条断言
`OPPORTUNITY_VIEW` 不在任何内置角色的授权集合里。

代价：将来"指定角色试用"（issue 里的第二步）时，需要专门写一条迁移把 `opportunity:view`
授予某个角色，并把它从 `SUPER_ONLY_PERMISSIONS` 里移出——`ROLES` 只是文档性质的矩阵，
不是运行时读取的授权来源（迁移各自硬编码 SQL，不 import 这个模块，见 `permissions.py`
头部注释），改 Python 字典本身不会自动同步进数据库,仍需要新迁移。

## 决策 2：机会中心是纯前端空状态页，不建任何后端端点

issue 验收标准第 4 条："机会中心在能力未完成时能解释当前开放范围、前置数据源和不可用
原因，不发起不存在的后端请求"。这与 `/ip` 两个占位屏的既有形态同构（`PendingDataSource`
组件），但机会中心的信息架构更复杂——issue 建议六个状态（待处理信号/候选机会/验证中/
风险待核查/已通过/已否决），不是单一空状态。

采用 `Tabs` 承载六个状态，每个 Tab 内容复用同一个空状态组件，文案按状态区分（例如
"待处理信号"写"信号计算尚未接入"，"已否决"写"验证流程尚未接入"），但结构上**不做
六个独立组件**——六个状态目前的差异只是文案，做六个组件是为一个还不存在的差异预先
分裂代码。等真实数据接入、状态之间开始出现结构性差异（比如"验证中"要显示进度条、
"已通过"要显示决策记录）时再拆，那时的拆分有真实依据。

不设 Tab 之间的路由（不做 `/opportunity/pending` 这类二级路径）——Tab 切换是纯前端
状态，issue 也明确"避免给宽表页面增加左侧二级菜单"，这里进一步不给单页顶层增加
基于 URL 的子状态，保持与其余单页顶层（`/analysis`、`/competitor`）同样的路由形状。

## 决策 3：`风险核查`（原侵权查询）不新增权限点

issue 表格里这一行写"数据源真正接入前，可保持仅超管可见"——"可"是选项不是强制。
本仓库已有两条更早的先例直接反对这个选项：

1. `top-nav-structure` spec 的"侵权查询菜单容器不设权限点"需求，理由是"没有端点可挂的
   权限点是'勾了也不生效'的复选框，与已被移除的 `user:manage` 同类"。
2. `user:manage` 本身的历史教训（CLAUDE.md「用户管理」一节）：可授予但不生效的权限点
   比没有更糟——管理员会以为把它授予了某个角色，那个角色就真的能用。

给 `风险核查` 挂一个"仅超管可见"的权限点，在没有端点的前提下，只是把"谁能点开一个
写着'数据源尚未接入'的页面"这件事变成可配置——这不是一个真实的授权需求，且与上面
两条先例的推理完全相同。因此这次只改菜单名，不新增权限点；等风险核查真的接入商标/
专利数据源、有了端点，那时再按 `opportunity:view` 或 `competitor:view` 的先例新增一个
真正生效的权限点。

## 决策 4：三处改名不动路由路径与组件目录

`ABA数据选品→关键词市场分析`、`侵权查询→风险核查`、`数据上传→数据管理`三处都只是
`routes.ts` 里 `name` 字段的文本改动。不同于上一次"工具箱→关键词工具"那次改名（那次
同时是分组结构的重组，因此连带了目录搬迁 `pages/Toolbox/` → `pages/Keyword/`），这次
三个菜单的路由、组件、子屏结构都不变，纯粹是标签重命名，不产生代码搬迁成本。

`analysis:view`/`competitor:view`/`upload:import` 三个权限点的 code 与既有语义都不变
——菜单名与权限点 code 本就允许不一致（`competitor:view` 早就与菜单名"竞对监控"用的是
两套词汇），改中文名不需要跟着改权限点 code。
