# 设计：分析查询屏的状态编排拆分

## 决策 1：依赖方向单向，`session ← scope ← query ← page`

```
useAnalysisSession        不 import 另外两个；只认快照与滚动
      ↑            ↑
useAnalysisScope   useAnalysisQuery      scope 不 import query，query 不 import scope
      ↑            ↑
        index.tsx                        唯一知道三者都存在的地方
```

三个 hook 之间**没有任何直接 import**。两处真实的跨组耦合都由 `index.tsx` 接线：

| 耦合 | 方向 | 接法 |
|---|---|---|
| `reloadScopeMeta` 要清掉类目与品牌筛选 | scope → query | scope 收一个 `onScopeInvalidated` 回调，页面传 `query.clearCategoryFilters` |
| `draftBody = toSearchBody(scope, filters)` | query → scope | `scope` 草稿作为参数传进 `useAnalysisQuery` |

**为什么不让 scope 直接持有 filters**（那样耦合就消失了）：类目既是作用域选项
（`categories` 清单，按站点/视图/日期取）又是筛选条件（`filters.categories`，用户勾的），
两者恰好同名但生命周期相反——清单跟着作用域走，勾选跟着用户走。合进一个 hook 会让
"这个 categories 是哪一个"重新变得要靠上下文判断，正是拆分要消除的东西。回调把方向钉死：
作用域失效时通知筛选，筛选永远不反向影响作用域。

**为什么 query 收的是 `scope` 而不是整个 scope hook**：它只读作用域三元组去组装
`SearchBody`，不需要 `sites` / `categories` / `reloadScopeMeta`。收窄到值而不是收整个
返回对象，"query 会不会偷偷去重拉作用域"这个问题就不用问了。

### 接线上的环，与它的代价

上表两条耦合方向相反，而 hook 的调用有先后：`useAnalysisScope` 要拿到
`query.clearCategoryFilters`，`useAnalysisQuery` 又要拿到 `scope.scope`。谁都不能先。

解法是把回调经一个 ref 间接化——scope 先建，收到的 `onScopeInvalidated` 指向一个此刻
还是空操作、渲染结束前就被填上的引用：

```ts
const clearCategoryFiltersRef = useRef<() => void>(() => {});
const scopeState = useAnalysisScope({ onScopeInvalidated: () => clearCategoryFiltersRef.current() });
const query = useAnalysisQuery({ scope: scopeState.scope, ... });
clearCategoryFiltersRef.current = query.clearCategoryFilters;   // 渲染期赋值
```

**这一步是安全的，因为 `reloadScopeMeta` 只从 effect 与事件处理器里调用**，那时赋值早已
完成；渲染期间没有任何路径会触发它。若将来有人在渲染期调用作用域重拉（那本身就是错的），
回调会静默地什么都不做——所以这条前提写在 `index.tsx` 的注释里。

代价是这个环无法靠"再拆一层"消除：真正消除它的唯一办法是把筛选并回作用域，而那正是
本次拆分要消除的东西。**接受一个显式的、注释清楚的 ref 间接，胜过一个隐式的、要靠上下文
判断的合并状态。**

## 决策 2：复原短路整段进 session，不留在 `request` 里

`request` 开头那段——`restored && !restoreServedRef.current` 一次性闸、`readSnapshot()`、
`sameRequest(key, cached.lastRequest)` 核对、命中则 `markDataReady()` 并返回缓存行——
四步全部收进 `session.consumeRestoredRows(key)`。

理由是这四步**必须一起改**：闸漏了会让每次翻页都吃缓存（用户点"立即查询"什么都不动）；
`sameRequest` 漏了会拿另一页的数据去填当前页；`markDataReady` 漏了滚动位置恢复不到。
它们分开写在 `request` 里时，三者中间隔着与短路无关的请求组装代码，读的人不容易看出
是一套。收成一个函数后，`request` 那侧只剩一行：

```ts
const restoredRows = session.consumeRestoredRows(key);
if (restoredRows) return { ...restoredRows, success: true };
```

`markDataReady` 仍然对外暴露：真实取数那条路径拿到行之后也要调它（滚动恢复的另一半
触发点）。

## 决策 3：`resolveInitialScope` 与挂载副作用留在 `index.tsx`

这段是本次拆分里唯一横跨三个 hook 的逻辑，它要按三级优先级（会话快照 > 保存的条件 >
内置默认）解析首屏：读 `session.restored` 决定要不要整段跳过、调 `scope.reloadScopeMeta`
（带 `keepCategories`）、失败时调 `query.setFilters` / `query.setSortDraft` /
`query.setSavedApplied` 回退、最后调 `query.apply` 查第一次。

**放进任何一个 hook 都会制造一条反向依赖**：进 scope 就要 import query 的 setter，进
query 就要 import scope 的 `reloadScopeMeta`——决策 1 那张图立刻出现回边。而它本来就是
issue #69 给 `index.tsx` 留的职责（"页面组合、跨模块协调"），协调三个模块的代码放在
协调者那里不需要额外理由。

同理，`saved`（`readSavedMode(userId)` 的一次性读取）也留在页面：它同时喂 scope 的初值
（站点/视图）与 query 的初值（筛选/排序），谁都不该独占。

## 决策 4：`actionRef` 归 query，不归页面

ProTable 的 `actionRef` 只有两个用途，`apply()` 与 `applySort()` 里的
`reload(true)`——两个都在 query 里。留在页面就要把 ref 当参数传进 hook，而"谁会去
reload 表格"这件事随即散在两处。页面只在 JSX 上写 `actionRef={query.actionRef}`。

## 决策 5：不给三个 hook 写单元测试

现有 57 条页面级用例已经覆盖这三组状态的全部对外行为，且**这次一行都不改**——它们正是
"行为未变"的判据。再写一层 `renderHook` 用例会把同一个口径写第二遍：口径漂移时两处
断言互相矛盾，而先信哪一个没有答案。

代价明确：hook 的内部实现细节（如 `dataTick` 的具体递增时机）没有直接断言，只能经页面
行为间接覆盖。接受这个代价，因为那些细节本来就不是契约。

**将来若某个 hook 被第二个屏复用**，那时它才有了独立于本屏的契约，也才到了该给它写
独立用例的时候。

## 决策 6：`CACHE_VERSION` 不动

`CachedValues` 与 `AnalysisSnapshot` 的字段一个没变，只是读写它们的代码换了文件。版本号
的作用是让**形状变化**退化成一次正常初始化；形状没变却 +1，只会让所有人的会话缓存白白
失效一次，还会给下一个人留下"这次变更改了快照结构"的错误印象。

## 不做什么

- **不拆 JSX**。过滤区那张卡片（约 124 行）里有一串实测得来的像素注释
  （`paddingInline: 24`、抵消 body 内边距的负 margin、与 `LABEL_WIDTH` 的右边缘对齐），
  搬运时注释必须整段跟走，漏了下一个人会"顺手改回去"。本次范围是状态编排，JSX 留待
  真要动那块外观时再说。
- **不动 `filters.ts` / `savedMode.ts` / `stateCache.ts` / `useCachedState.ts`**。它们本来
  就是边界清楚的纯模块，是这次拆分的落点而不是对象。
- **不改任何默认值、口径或事实来源**。`defaultFilters()`、`FILTER_KEYS`、
  `SELECTION_COLUMN_WIDTH` 等各自仍然只有一处定义，迁移时整体移动、不复制。
