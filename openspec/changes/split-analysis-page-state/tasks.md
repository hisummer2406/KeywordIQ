## 0. 前置

- [x] 0.1 基线判据定为**四个测试文件与 `__testing__/` 的 diff 为空**（比"用例数相同"更硬：
      用例数相同也可能是改了断言内容）。前端 lint/tsc/test 由提交门禁与 CI 执行，
      本变更不自己跑（见 CLAUDE.md「测试按影响面跑」与用户既定分工）

## 1. `useAnalysisSession.ts`：会话快照基座 + 滚动

- [x] 1.1 惰性读一次快照，导出 `initialSnapshot` 与 `restored`
- [x] 1.2 `mounted` ref（effect 里置位，不用 `useRef(true)` 一次性赋值——StrictMode 下
      setup → cleanup → setup 会跑两轮）+ `persist` 闸，注释整段跟走
- [x] 1.3 滚动量节流持久化（scroll 监听 + rAF 合帧）；**不改成 cleanup 里读一次**
- [x] 1.4 滚动位置恢复：`pendingScrollRef` / `dataTick` / `markDataReady` + 逐帧等页面
      长够再滚的 effect（含 30 帧上限）
- [x] 1.5 `consumeRestoredRows(key)`：一次性闸 + `readSnapshot()` + `sameRequest` 核对 +
      命中则 `markDataReady()` 并返回 `{ data, total }`，否则 `null`（design 决策 2）

## 2. `useAnalysisScope.ts`：作用域与选项

- [x] 2.1 owns `scope` 草稿（`useCachedState`）、`sites` / `availableDates` /
      `categories` / `scopeLoaded`
- [x] 2.2 `loadSites` / `reloadCategories` / `reloadScopeMeta` / `onScopeChange` 迁入，
      注释整段跟走（尤其 `keepCategories` 那段与"只换日期不清类目"那段）
- [x] 2.3 `reloadScopeMeta` 的清类目分支改调传入的 `onScopeInvalidated` 回调，
      **不 import query**（design 决策 1）
- [x] 2.4 派生 `scopeHasNoDates`（`scopeLoaded` 前置不能去掉）

## 3. `useAnalysisQuery.ts`：查询编排

- [x] 3.1 owns `filters` / `sortDraft` 草稿，生效态三件套 + `appliedSortRef`，
      总数缓存三件套，`pendingRebuild` / `exporting` / `savedApplied` / `actionRef`
- [x] 3.2 `apply`（含 `competitor_new` 权限兜底与 `sig` 本函数统一重算）/ `submitScope` /
      `submitFilters` / `resetFilters` / `saveCurrentMode` / `onExport` / `applySort`
- [x] 3.3 `clearCategoryFilters`：草稿与生效**两份都清**，供 scope 的回调调用
- [x] 3.4 `runRequest(params)`：先问 `session.consumeRestoredRows`，再走真实取数；
      `needsTotal` 在**成功响应之后**才清
- [x] 3.5 `FILTER_KEYS` 与 `appliedHidesDropped` 迁入；派生 `draftBody` / `draftSig` /
      `dirty` / `hasQueried`

## 4. `index.tsx` 收缩

- [x] 4.1 `saved` 一次性读取留在页面，分别喂 scope 与 query 的初值
- [x] 4.2 `resolveInitialScope` + 挂载副作用留在页面（design 决策 3），`restored` 那道
      闸仍在最前面
- [x] 4.3 三个 hook 接线：`onScopeInvalidated` → `query.clearCategoryFilters`；
      `scope.scope` → `useAnalysisQuery`
- [x] 4.4 `appliedDomain` / `columns` 两个 memo 保留在页面
- [x] 4.5 JSX 原样保留，只把引用换成 hook 上的字段

## 5. 验证

- [x] 5.1 `git diff --stat` 确认四个测试文件与 `__testing__/` 未被修改（0.1 的判据）
- [x] 5.2 提交门禁：`pnpm lint` + `pnpm tsc` + `pnpm test` 全绿（`git commit` 时自动跑，
      不通过就拒绝提交——这是本变更"行为未变"的机器判据）。已额外核对门禁确实认出了
      这次的路径式提交（`target_repo` 解析到 worktree、`_has_pathspec` 为真因而带上
      未暂存的 `index.tsx`），不是"一条检查都没跑就放行"
- [ ] 5.3 CI（PR #71）：前端 lint + tsc + test + **build**（`pnpm build` 只在 CI 跑，
      issue #69 验收标准点名）
- [ ] 5.4 人工实测交给用户，五项（PR #71 正文有清单）：首屏自动查询（默认只看新词）、
      切走再切回一个请求都不发且滚动位置回到离开前、保存/重置条件、导出 CSV、
      改站点后类目清空
