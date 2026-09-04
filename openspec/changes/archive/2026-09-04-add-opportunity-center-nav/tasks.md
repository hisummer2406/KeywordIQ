# 任务

> 决策已定（见 design.md）：`opportunity:view` 不授予任何内置角色、机会中心不建后端
> 端点、风险核查不新增权限点、三处改名不搬迁目录。动手前不需要再对齐。

## 1. spec 与提案

- [x] 1.1 `specs/opportunity-center/spec.md`：新增能力，覆盖权限点默认仅超管、单页
      顶层形态、能力未完成阶段的空状态约束
- [x] 1.2 `specs/top-nav-structure/spec.md`：RENAMED「侵权查询菜单容器不设权限点」→
      「风险核查菜单容器不设权限点」；MODIFIED 六项顺序、单页顶层清单加入机会中心
- [x] 1.3 `openspec validate add-opportunity-center-nav --strict` 通过

## 2. 后端：权限点

- [x] 2.1 `app/auth/permissions.py`：新增 `OPPORTUNITY_VIEW = "opportunity:view"`，
      加入 `PERMISSIONS` 字典（中文名「查看机会中心」）。**落地时发现一个未在 design.md
      原稿里预料到的陷阱**：`ROLES["admin"]` 历来是 `set(PERMISSIONS.keys())`（动态取
      全部权限），若不处理会自动把新权限点吸收进 admin 角色。已加 `SUPER_ONLY_PERMISSIONS`
      集合、`ROLES["admin"]` 改为 `set(PERMISSIONS.keys()) - SUPER_ONLY_PERMISSIONS`，
      design.md 决策 1 已同步补上这段推导
- [x] 2.2 Alembic 迁移 `533cd79ca75f`（down_revision=`7b8b046c075e`）：只
      `INSERT INTO permission`，不写 `role_permission`；docstring 写明"不授予任何角色，
      仅 is_super 可见"
- [x] 2.3 迁移可回滚性：`alembic upgrade head && alembic downgrade -1 && alembic upgrade head`
      在 `keywordiq_test` 上跑通（已实测，见下方收尾记录）
- [x] 2.4 测试：`test_rbac_auth_unit.py` 新增 `test_opportunity_view_is_super_only`——
      三个内置角色都不持有 `opportunity:view`；`is_super=true` 的用户放行；admin 角色但
      `is_super=false` 的用户被拒。`test_role_permission_matrix_matches_spec` 同步改写
      （admin 断言从"等于全部权限"改为"等于全部权限减去 SUPER_ONLY_PERMISSIONS"）

## 3. 前端：菜单结构

- [x] 3.1 `frontend/src/routes.ts`：新增「机会中心」路由节点（`/opportunity`，单页
      顶层，`access: 'canViewOpportunity'`），排在最前；「ABA数据选品」改名「关键词
      市场分析」、「侵权查询」改名「风险核查」、「数据上传」改名「数据管理」（均只改
      `name` 字段，路径/组件/access 不变）；相关注释同步改写称呼。同时改了三处用户可见
      文案（`Analysis/index.tsx`、`Keyword/KeywordLibrary/index.tsx`、
      `Keyword/KeywordLibrary/GroupBar.tsx`）里引用「ABA数据选品」旧名的地方，以及
      `global.less` 的一处注释
- [x] 3.2 `frontend/src/access.ts`：新增 `canViewOpportunity: has('opportunity:view')`，
      注释说明这是当前唯一"默认不授予任何内置角色"的权限点
- [x] 3.3 `frontend/src/pages/Opportunity/index.tsx`：单页顶层，`PageContainer` +
      `Tabs`（六个状态），每个 Tab 内容复用同一个空状态展示组件，文案区分阶段说明并
      点出各自依赖的 issue 号，顶部另加一条 `Alert` 说明这是骨架阶段；不发任何请求
- [x] 3.4 `frontend/src/routes.test.ts`：顶栏一级菜单断言改为六项新顺序与新名字；
      新增机会中心单页顶层（无 `routes`）与 `access` 的断言；风险核查/数据管理沿用
      既有的"无权限点"/"权限不变"断言，只换名字
- [x] 3.5 `pnpm lint && pnpm tsc` 全绿；`vitest related --run`（routes/access/Opportunity/
      Analysis/KeywordLibrary/GroupBar 六个源文件）8 个测试文件 94 个用例全过。**全量
      `pnpm test` 未跑**——按仓库约定前端全量测试是提交门禁与 CI 的职责，不在实现阶段
      自行跑

## 4. 文档

- [x] 4.1 CLAUDE.md「顶栏菜单：按数据域命名，不设按排除法命名的容器」一节：顶栏顺序
      改为六项；补充机会中心的权限策略（默认仅超管）、`SUPER_ONLY_PERMISSIONS` 陷阱、
      "为什么不给风险核查加权限点"的重申；三处改名同步。「Authentication & Authorization」
      一节的权限点清单从五个改为六个，并补一句 `opportunity:view` 是例外
- [x] 4.2 `.env.example` 核对：本变更不新增配置项，无需改动

## 5. 收尾

- [x] 5.1 ruff 全仓库通过；后端定向测试通过（`test_rbac_auth_unit.py` 19 个 +
      `test_conventions.py` 全部 + 关键词 rbac/permission/route 相关共 41 个用例）；
      前端 lint/tsc 通过，routes.test.ts 8 个用例通过，related 测试 94 个用例通过
- [x] 5.2 人工验收清单已转移至 issue #72（本次实现未跑，需要用户在浏览器里核对）
- [x] 5.3 按路径提交，开 PR `--base v2`（PR #77），说明关联 issue #76；已合并
