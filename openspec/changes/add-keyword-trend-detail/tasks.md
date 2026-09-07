## 0. 开工前置

- [x] 0.1 `openspec list` / `git branch --list 'change/*'` / `git worktree list` /
  `gh pr list --base v2` 确认无人在做同一件事（issue #86）
- [x] 0.2 建仓库外 worktree `~/.claude-worktrees/add-keyword-trend-detail` 与分支
  `change/add-keyword-trend-detail`（从 `v2`），补 `.env`、`frontend/node_modules`；
  确认 `keywordiq_test` 存在
- [x] 0.3 基线回归：`843 passed, 1 skipped`（`TEST_DATABASE_URL` 指向 `keywordiq_test`，
  集成测试确认在跑；唯一跳过是需 `TEST_OSS_*` 的真实 OSS 回路，与本变更无关）

## 1. 后端：错误码与权限原语

- [x] 1.1 `app/common/errors.py` 新增 `CODE_KEYWORD_TREND_NOT_FOUND`、
  `CODE_KEYWORD_TREND_SITE_MISMATCH`
- [x] 1.2 `app/auth/simple_auth.py` 新增 `SimpleAuth.require_any_permission(*permissions)`：
  持有任一即放行，`is_super` 照常兜底；与 `require_permission` 同构
- [x] 1.3 单元测试：`require_any_permission` 的三种组合（只 A / 只 B / 都没有 / is_super）
  ——`test/test_rbac_auth_unit.py`

## 2. 后端：查询与五态分类

- [x] 2.1 `app/table/keyword/trend_schemas.py`：请求/响应 DTO
  （`KeywordTrendPoint`、`KeywordTrendResponse`，`range: Literal["RECENT_30D","ALL"]`）
- [x] 2.2 `app/table/keyword/trend_crud.py`：`keyword_history_stmt`（走
  `idx_fact_*_keyword_history`）、`keyword_dropped_stmt`（走 `fact_dropped` 主键）、
  `keyword_site_stmt`（校验用）——复用 `analysis_crud.fact_and_datecol`，不改动
  `analysis_crud.py`
- [x] 2.3 `app/table/keyword/trend_service.py`：
  - 站点/keyword_id 一致性校验（不一致 422）
  - 锚定期解析：`_resolve_anchor` 取 `<= on` 的最新一期，取不到退化为全局最新一期
    （design 决策 4，同时覆盖同视图与跨视图两种情形，不需要分支）
  - 期望期次枚举（`RECENT_30D`/`ALL`，步长按视图取 1/7 天，`_expected_dates`/`_floor_for_range`）
  - `retention_cutoff` 优先于 `site_data_stats` 的五态分类（design 决策 2）
  - `best_ranking`/`latest_available` 统计（只取 RANKED 点）
- [x] 2.4 `app/table/keyword/trend_api.py`：`GET /keyword/trend`，挂
  `require_any_permission(ANALYSIS_VIEW, KEYWORD_VIEW)`；`api_router.py` 注册
- [x] 2.5 单元测试（`test/test_keyword_trend_unit.py`，13 条）：五态分类依赖的纯函数
  边界——`_resolve_anchor` 精确匹配/跨视图映射/退化分支/空集合，`_expected_dates`
  日/周步长与单点，`_floor_for_range` 的 RECENT_30D/ALL（含"site_data_stats 残留
  超期行"的防御场景，以及"锚点本身就是残留超期行"这种会让下界越过锚点、
  `_expected_dates` 回传空列表的边界——这条测试写完后确实抓到了一处会让
  `expected[0]` IndexError 的真实缺陷，已在 `_floor_for_range` 里补一次
  `min(earliest, anchor)` 修掉）
- [x] 2.6 集成测试（`test/test_keyword_trend_integration.py`，9 条，`TEST_DATABASE_URL`
  下全部通过）：真实分区数据验证五态分类、`OUT_OF_RETENTION` 优先于残留的
  `site_data_stats` 行、`range=ALL` 且锚点本身超期时不崩溃（2.5 那处缺陷的端到端
  复现）、区间统计、跨站点 422、未知 keyword_id 404、无数据时的空响应。
  另有 API 层测试 `test/test_keyword_trend_api_unit.py`（6 条）覆盖权限组合与错误映射。
  `EXPLAIN ANALYZE` 已在 `keywordiq_v2_dev`（410 万行 fact_daily、373 万行
  fact_dropped）上核对：两条查询均走 `Index Scan`（`idx_fact_daily_keyword_history`
  与 `fact_dropped` 主键/掉榜分页索引），无 Seq Scan，执行时间 ~9ms / ~3ms
  （证据见 PR 描述）

## 3. 前端：服务层与抽屉组件

- [x] 3.1 `frontend/src/services/keywordTrend.ts`：`getKeywordTrend(...)` 客户端
- [x] 3.2 `frontend/src/components/KeywordTrendDrawer/`：
  - 底部抽屉容器，接收 `{ keywordId, keyword, site, view, on, hideAddToLibrary? }`
  - `TrendTimelineChart.tsx`：真实时间轴（按日期线性映射，不等距），折线只连数组中
    相邻且都是 RANKED 的两点，五态各自标注
  - 统计条：所选范围锚定期、实际覆盖范围、区间最好排名、最近可用排名（含各自日期）
  - 范围切换（近 30 天 / 全部可用历史）与日/周切换均复用同一个来源锚点 `on`，用递增
    请求序号防止旧请求覆盖新结果（未用 AbortController——umi `request` 无现成取消口子）
  - 「加入词库」按钮，复用 `AddToLibraryModal`；`hideAddToLibrary` 时不渲染
- [x] 3.3 vitest（未在本会话执行，见任务 5.2 说明）：
  `TrendTimelineChart.test.tsx`（6 条：无数据/全非在榜/相邻连线/隔缺口不连线/单点/
  掉榜不连线）、`index.test.tsx`（8 条：来源锚点请求、无有效点显示无数据、加载失败
  不等同无数据、切视图复用同一锚点、旧请求不覆盖新结果、`hideAddToLibrary` 两态）

## 4. 前端：接入两个入口

- [x] 4.1 `frontend/src/pages/Analysis/columns.tsx`：`buildColumns` 加可选
  `onOpenTrend` 回调，趋势单元格（含占位符）整体可点击
- [x] 4.2 `frontend/src/pages/Analysis/index.tsx`：接入抽屉状态，来源锚点取
  `appliedRef.current.date` 与 `appliedView`/`appliedRef.current.site`
- [x] 4.3 `frontend/src/pages/Keyword/KeywordLibrary/columns.tsx`（`buildWatchColumns`
  透传同一个 `onOpenTrend`）与 `index.tsx`（来源锚点取当前 `date`/`view`/`active.site`，
  传 `hideAddToLibrary`）
- [x] 4.4 vitest（未执行，见 5.2）：`columns.test.tsx` 新增 3 条覆盖点击入口与无小图
  行同样可点开

## 5. 收尾

- [x] 5.1 CLAUDE.md 补一节：端点契约、五态分类算法要点、`require_any_permission` 的
  用法与"何时该用它而不是新增权限点"的判据
- [ ] 5.2 全量回归：**后端已跑**（`uv run pytest test/ -q`，`TEST_DATABASE_URL` 指向
  `keywordiq_test`）：`874 passed, 1 skipped`（较基线 843 新增 31 条，跳过项同基线、
  与本变更无关）；`uv run ruff check .` 全部通过。**前端 vitest/tsc/lint 本会话未跑**
  ——按仓库既定约定（[[run-targeted-tests-not-full-suite]]）这类命令留给用户或 CI 执行，
  新增/改动的前端文件已列在本文件第 3、4 组，供 CI 跑一遍核对
- [ ] 5.3 提交、开 PR（`--base v2`），关联 issue #86，附 spec delta 文件列表、
  查询计划验证证据、已知未覆盖风险
