## Why

`frontend/src/pages/Analysis/index.tsx` 有 919 行，同时承担七件事：会话快照的读写与
卸载后的写入闸、滚动位置的持久化与恢复、草稿态与生效态两份查询条件、作用域元数据
（站点/日期/类目）的拉取与失效、首屏按三级优先级解析初始条件、结果表格的请求与分页
总数缓存、以及整屏的 JSX 组合。

这些职责之间没有共享的生命周期：滚动恢复只关心"行有没有落进 DOM"，作用域元数据只关心
"站点或视图变了没有"，草稿/生效双态只关心"用户点没点查询"。但它们全都写在同一个函数体
里，读的人无法只理解其中一件——改滚动恢复要先读懂快照闸，改类目失效要先读懂生效态。
这正是 issue #69 记录的三个局部热点之一。

本变更是 issue #69 建议实施顺序的第二阶段：把这三组状态各自抽成一个 hook，`index.tsx`
只保留跨模块协调（首屏解析）与 JSX 组合。

**不改变任何被测行为。** 现有的 57 条页面级用例（`defaultsAndFirstPaint` /
`scopeAndFilters` / `exportAndPaging` / `sessionAndTable`）**一行都不改**，它们就是这次
重构的安全网：这些用例覆盖首屏自动查询、默认只看新词、保存的默认条件、切走再切回的复原
短路、加入词库、总数实算与翻页、导出——正是 issue #69 验收标准点名的那几项。用例不改而
全绿，就是"行为未变"的证据；要改用例才能过，说明拆错了。

## What Changes

- **新增 `useAnalysisSession.ts`**：会话快照基座 + 滚动。owns `initialSnapshot`（惰性读
  一次）、`restored`、卸载后不再写快照的 `persist` 闸、滚动量的节流持久化、滚动位置的
  逐帧恢复（`markDataReady` / `pendingScrollRef` / `dataTick`），以及**复原短路**
  ——`restoreServedRef` 那道一次性闸连同 `sameRequest` 核对，收成一个
  `consumeRestoredRows(key)`，命中就返回缓存行并 `markDataReady`，否则返回 `null`。
  短路语义（只作用于复原后第一次请求、必须核对完整请求标识）因此只有一处定义。

- **新增 `useAnalysisScope.ts`**：作用域与选项。owns `scope` 草稿、`sites`、
  `availableDates`、`categories`、`scopeLoaded`，以及 `loadSites` / `reloadCategories` /
  `reloadScopeMeta` / `onScopeChange` 与派生的 `scopeHasNoDates`。

- **新增 `useAnalysisQuery.ts`**：查询编排。owns `filters` 草稿、`sortDraft`、生效态三件套
  （`appliedRef` / `appliedView` / `appliedSig`）、`appliedSortRef`、总数缓存
  （`totalRef` / `needsTotalRef` / `resultTotal`）、`pendingRebuild`、`exporting`、
  `savedApplied` 与 ProTable 的 `actionRef`；对外给 `apply` / `submitScope` /
  `submitFilters` / `resetFilters` / `saveCurrentMode` / `onExport` / `applySort` /
  `runRequest`，以及派生的 `draftBody` / `draftSig` / `dirty` / `hasQueried` /
  `appliedHidesDropped`。`FILTER_KEYS` 随 `appliedHidesDropped` 一起迁入。

- **`index.tsx` 收缩到约 400 行**：只剩 `saved` 的一次性读取、`resolveInitialScope` 与
  挂载副作用（跨三个 hook 的协调，见 design.md 为什么它不进任何一个 hook）、
  `appliedDomain` / `columns` 两个跨模块 memo，以及 JSX。

- **快照结构不变，`CACHE_VERSION` 保持 2**：本变更只改"谁来读写这些键"，`CachedValues`
  与 `AnalysisSnapshot` 的形状一字未动。版本号是给形状变化用的，这里 +1 只会让所有人的
  会话缓存白白失效一次。

## Capabilities

### New Capabilities

无。这三个 hook 是页面内部的组织方式，不是可被别处复用的能力契约——它们的参数与返回值
形状会随这一屏的需要变，写成 requirement 只会把一份即将变动的实现细节钉死。

### Modified Capabilities

无。不改变任何 API 路径、请求/响应契约、权限点、页面文案或数据口径；`analysis-query`
等既有 spec 描述的行为逐条不变。因此本变更**没有 `specs/` 目录**。

## Impact

**受影响文件**（全部在 `frontend/src/pages/Analysis/` 下）：
- 新增 `useAnalysisSession.ts` / `useAnalysisScope.ts` / `useAnalysisQuery.ts`
- `index.tsx`：919 行 → 约 400 行，逻辑迁出，JSX 与协调保留

**不受影响**：后端全部、`filters.ts` / `savedMode.ts` / `stateCache.ts` /
`useCachedState.ts` / `columns.tsx` 与全部展示组件（`ScopeBar` / `CategoryPanel` /
`FilterPanel` / `AddToLibraryModal`）、四个页面级测试文件与 `__testing__/` 下的共用替身。
