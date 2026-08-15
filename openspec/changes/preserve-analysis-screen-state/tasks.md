> **验证命令说明**：本变更是纯前端改动，不含 Python 代码，`uv run pytest test/` 对它没有
> 可断言的内容。等价的验证是 `cd frontend && pnpm test`（vitest）+ `pnpm lint` + `pnpm tsc`，
> 与提交门禁对 `frontend/` 改动所跑的一致。下文的测试任务均指这一套。

## 1. 缓存模块与纯函数（先写测试）

- [x] 1.1 写 `frontend/src/pages/Analysis/stateCache.test.ts`：覆盖「无缓存时 `readSnapshot` 返回空」「写入后能读回」「版本号不匹配视同无缓存」「`clearSnapshot` 后读回为空」四种情形
- [x] 1.2 实现 `frontend/src/pages/Analysis/stateCache.ts`：模块级快照 + `version` 常量 + `readSnapshot` / `writeSnapshot` / `clearSnapshot`；快照键以字面量联合类型声明（写错 key 即 TypeScript 报错）
- [x] 1.3 写 `sameRequest(a, b)` 的测试：生效条件 / 排序字段 / 排序方向 / 页码 / 每页条数**任一不同即为 false**，全同为 true；并覆盖「条件对象键序不同但内容相同」应判为 true
- [x] 1.4 实现 `sameRequest`，与 `stateCache` 同文件或相邻纯函数模块，不引入 React 依赖

## 2. 状态改由缓存承载

- [x] 2.1 实现 `useCachedState(key, initial)`：初值取自快照（无则用 `initial`），`set` 时 write-through 写回快照
- [x] 2.2 把 `AnalysisPage` 里的 `useState` 换成 `useCachedState`（作用域草稿、过滤草稿、生效签名、生效视图、站点、日期清单、类目、排序草稿、过滤区展开态、结果总数）
- [x] 2.3 `appliedRef` / `totalRef` / `appliedSortRef` / `needsTotalRef` 的初值改为从快照读，赋值处写回快照
- [x] 2.4 手动核对：草稿态与生效态在快照里是**两个独立键**，没有被合并（这条弄错会毁掉「表格显示的是哪组条件」的可读性）

## 3. 挂载副作用加闸与请求短路

- [x] 3.1 挂载副作用改为：快照中已有生效条件 → 整段跳过（不拉 sites / scope / categories，不调 `apply`）；否则走现行初始化
- [x] 3.2 ProTable 的 `request` 加短路分支：`sameRequest(本次参数, 快照中那次)` 为真且快照有结果行 → 直接返回缓存的 `{data, total, success}`，不发请求
- [x] 3.3 `request` 每次真实取数后把「本次请求参数 + 结果行 + 总数」写回快照
- [x] 3.4 核对「改站点或视图立即重拉 `/scope` 与 `/categories`」的既有逻辑未被改动——复原只跳过挂载初始化，不影响用户动作触发的请求

## 4. 滚动位置

- [x] 4.1 记录滚动量：在 `window` 的 scroll 事件上节流写回快照（或在导航离开前写一次），确保切走时快照里有值
- [x] 4.2 复原时置「待恢复滚动量」标记，在结果行落到 DOM 之后的 effect 里执行一次 `window.scrollTo`，随即清掉标记
- [x] 4.3 手测：滚到列表中段切走再切回，停在原位置；随后在本屏内自行滚动，不会被弹回
      —— 2026-08-16 由使用者在 8002 上人工确认通过。浏览器自动化驱动不了真实滚动
      （滚轮与 Page_Down 都不动页面，程序化 scrollTo 不派发 scroll 事件），故这一条
      只能人工验；接线另有单元测试守住（「复原后按离开前的滚动量调用 scrollTo」
      「首次进入不动滚动位置」）。

## 5. 登出清空

- [x] 5.1 `src/app.tsx` 的 `doLogout` 增加第四步 `clearSnapshot()`，与清令牌 / 清 initialState / 回登录页并列
- [ ] 5.2 手测：A 登录查数据 → 登出 → B 在同一标签页登录进分析屏，看到的是默认首屏，没有 A 的条件与结果
      —— 待验。2026-08-16 已建测试账号：operator1 / analyst1 / admin2（密码均 pwd123），
      可用其中任意两个账号走一遍。清空调用点已接在 doLogout 上。

## 6. 验证与收尾

- [x] 6.1 `cd frontend && pnpm test`（vitest）全绿，新增的纯函数测试包含在内
- [x] 6.2 `pnpm lint` 与 `pnpm tsc` 通过
- [x] 6.3 浏览器实测四条 spec 场景：改条件切走切回零请求（拦 XHR 确认）、草稿改动保留、翻页与排序保留、刷新页面回到默认初始化
- [x] 6.4 更新 CLAUDE.md 的分析查询屏一节：写明「切回来不刷新」是约定行为，以及缓存在登出时清空

## 尾注

- 2026-08-15：实现完成。`pnpm lint` / `pnpm tsc` / `pnpm test`（237 个，新增 43 个）全绿。
- 浏览器实测（worktree dev server，端口 8002）已确认：切回零请求、作用域与草稿条件原样保留、
  表格显示离开前的行、**第 3 页与首行原样保留**。
- 实现期间发现并修掉一个设计缺陷：短路条件最初写成「参数相同就用缓存」，会把同参数的
  「立即查询」也吃掉——那是用户强制刷新的唯一入口。改为只对复原后的第一次请求短路（一次性）。
  由既有用例「顶部立即查询不会偷带尚未应用的筛选条件」抓到。
- 与本变更无关的现象一并记在这里：本地库没跑迁移，`/api/toolbox/growth/search` 报
  `relation "analysis.fact_growth" does not exist`（500）。跑 `uv run alembic upgrade head` 即可。

## 7. 首屏不查数据表（2026-08-16 追加）

- [x] 7.1 挂载副作用去掉 `apply(...)`，只保留 `reloadScopeMeta`（站点/日期/类目仍要拉，它们是选择器的前提）
- [x] 7.2 未查询时不显示「搜索结果」条数；空表格给出「点立即查询」的提示，与「查出来 0 行」区分开
- [x] 7.3 共用的 `renderSettled` 默认替测试点一次「立即查询」，验首屏行为的用例传 `query=false`
- [x] 7.4 新增首屏回归 5 条：只拉元数据不查表 / 不显示条数 / 空状态文案 / 点了才发请求 / 没查过就切走切回仍不查
- [x] 7.5 同步 spec delta、proposal 与 CLAUDE.md（原先写的「首次进入照常查询默认条件」已被推翻）
