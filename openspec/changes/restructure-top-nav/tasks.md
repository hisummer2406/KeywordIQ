# 任务

> 决策已定（见 design.md）：侵权查询进顶栏、关键词工具不并进选品、权限点改名
> `keyword:view`。动手前不需要再对齐。

## 1. spec 与提案

- [x] 1.1 `specs/top-nav-structure/spec.md`：新增能力，覆盖顶栏五项结构与排序、
      单页顶层不长侧栏的约束、关键词工具容器、侵权查询容器与占位屏、`keyword:view` 权限点
- [x] 1.2 `specs/toolbox-navigation/spec.md`：REMOVED 三条需求（该能力退休）。
      **归档时要连带删除 `openspec/specs/toolbox-navigation/` 目录**——`openspec archive`
      不一定会删掉变空的能力目录，留个空文件下次扫描会当成还在的能力
- [x] 1.3 `specs/growth-market-list/spec.md`：MODIFIED 端点那条需求，权限码换 `keyword:view`。
      **MODIFIED 是整条替换**，块里没写的 Scenario 归档时会被丢掉，三条 Scenario 一条都不能少
- [x] 1.4 `specs/keyword-library/spec.md`：MODIFIED 进屏即查那条，路径换 `/keyword/library`（同上，整条替换）
- [x] 1.5 `specs/competitor-storefront-watch/spec.md`：MODIFIED「权限点独立于工具箱」，
      标题与正文里的 `toolbox:view` 换成 `keyword:view`，措辞不再依赖「工具箱」这个已不存在的名字
- [x] 1.6 `openspec validate restructure-top-nav --strict` 通过

## 2. 后端：权限点改名 `toolbox:view` → `keyword:view`

- [x] 2.1 `app/auth/permissions.py`：`TOOLBOX_VIEW` → `KEYWORD_VIEW = "keyword:view"`，
      中文名「查看关键词工具」，三个内置角色的集合同步；注释改写为「为什么是容器级共用点、
      为什么不逐屏拆」，并说明它与被删除的 `user:manage` 不是一回事（这个是真执行的）
- [x] 2.2 Alembic 迁移：`UPDATE analysis.permission SET code, name WHERE code='toolbox:view'`，
      降级反向 UPDATE。**不动 `role_permission`**（它按 `permission.id` 关联，见 design 决策 3），
      并在迁移 docstring 里写明这一点，免得下一个人以为漏了
- [x] 2.3 `growth_api.py` / `library_api.py` 的 `require_permission` 换成新常量
- [x] 2.4 测试：`test_rbac_auth_unit.py` 与 `test_protable_contract.py` 里的权限码断言更新；
      新增一条回归——三个内置角色都持有 `keyword:view`，且系统中不再存在 `toolbox:view`
- [x] 2.5 迁移可回滚性：`alembic upgrade head && alembic downgrade -1 && alembic upgrade head`
      在 `keywordiq_test` 上跑通

## 3. 后端：API 前缀与包改名（**可整组砍掉**，见 design 决策 5）

- [x] 3.1 `app/table/toolbox/` → `app/table/keyword/`，模块 docstring 同步
- [x] 3.2 `app/api_router.py` 的前缀 `/api/toolbox` → `/api/keyword`
- [x] 3.3 `frontend/src/services/toolbox.ts` → `keyword.ts`，请求路径同步；`library.ts` 的路径同步
- [x] 3.4 后端测试里的端点路径全量更新（`test_growth_query_*`、`test_keyword_library_*`、
      `test_protable_contract.py`）
- [x] 3.5 不适用——本组未被砍掉，不存在前缀分叉，无需记录
- [x] 3.6 顺带：`CODE_TOOLBOX_SCOPE_INVALID` → `CODE_KEYWORD_SCOPE_INVALID`
      （`keyword.scope_invalid`，两屏共用），并删掉 `CODE_TOOLBOX_SORT_INVALID`——
      它自声明起就无人引用（非法排序早已由 schema 的 Literal 挡在参数校验层）

## 4. 前端：菜单结构

- [x] 4.1 `frontend/.umirc.ts`：顶栏五项按新顺序重排；`工具箱` 节点改为 `关键词工具`
      （`/keyword`，两个子项）；新增 `侵权查询`（`/ip`，两个子项）。父路由各自 redirect
      到第一个子项。**落地时 `侵权查询` 干脆不写 `access`**（原计划写 `'isLogin'`）：
      外层 ProLayout 本就要求已登录，多写一个只会让人以为那里有一道额外的闸
- [x] 4.2 旧地址重定向：`/toolbox`、`/toolbox/growth`、`/toolbox/library` 三条，`hideInMenu`；
      两个占位屏不留（design 决策 4）
- [x] 4.3 路由注释重写：现在的注释在解释「工具箱装什么」，要换成解释新的分组判据
      （按数据域命名）、以及**为什么关键词工具没有并进选品**（侧栏宽度，design 决策 2）
- [x] 4.4 `frontend/src/access.ts`：`canViewToolbox` → `canViewKeyword`（`keyword:view`）；
      侵权查询不加权限工厂项，直接用 `isLogin`，并注释说明为什么不给它权限点
- [x] 4.5 页面目录：`pages/Toolbox/{GrowthMarket,KeywordLibrary}` → `pages/Keyword/`，
      `pages/Toolbox/{DesignPatent,Trademark,PendingDataSource.tsx}` → `pages/IP/`；
      import 路径与 `.umirc.ts` 的 component 同步
- [x] 4.6 前端测试：现有用例里的路径与权限码更新（`pages/Analysis/sessionAndTable.test.tsx`
      引用了 toolbox），新增一条断言顶栏五项的顺序与名字
- [x] 4.7 `pnpm lint && pnpm tsc && pnpm test` 全绿

## 5. 文档

- [x] 5.1 CLAUDE.md：「工具箱 · 持续增长市场」「工具箱 · 我的词库」两节改名并更新端点路径；
      占位屏那段改写为独立的「侵权查询」说明；权限点清单从四个改为对应的新集合
- [x] 5.2 CLAUDE.md 新增一句话记下**顶栏分组判据**（按数据域命名，不设按排除法命名的容器），
      否则下一个新功能又会面对「它属于哪儿」这个没有答案的问题
- [x] 5.3 `.env.example` 核对（本变更不新增配置项，确认无需改动即可）

## 6. 收尾

- [ ] 6.1 `uv run ruff check .` + `uv run pytest test/ -q`（带 `TEST_DATABASE_URL`）全绿
- [ ] 6.2 人工验收：五项菜单各点一遍；旧地址 `/toolbox/growth` 能落到新屏；
      用一个只有 `analysis:view` 的账号确认关键词工具被挡回、侵权查询可见
- [ ] 6.3 按路径提交（先看 `git status --short` 的索引列），开 PR `--base v2`

### 4 组落地时的一处偏离

4.6 原计划直接在测试里 import `.umirc.ts`，**做不到**：那条链经 `@umijs/max` 拽进
esbuild，jsdom 下启动即抛「your JavaScript environment is broken」（jsdom 的 TextEncoder
产出的不是真 Uint8Array）。改成把 routes 数组抽成 `frontend/src/routes.ts`，`.umirc.ts`
import 它。副作用是好的：菜单结构从此可测，而它原本是**改错了不会有任何地方报错**的那类
东西。已用 `max setup` 核对 umi 生成的 `.umi/core/route.tsx` 与预期一致。
