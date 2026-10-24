> 2026-10-24 Claude 接手 Codex 在 `/private/tmp` 留下的未提交草稿（按 B0 格式推断，未按 #150 方案），工作区迁至 `~/.claude-worktrees/multi-asin-search` 后按本变更重做。

## 1. 规格

- [x] 1.1 `analysis-screen-layout`：REMOVED「单一检索框按输入格式判定检索目标」，ADDED「检索目标由显式选择的搜索方式决定」，MODIFIED「每个筛选维度在界面上恰好有一个入口」
- [x] 1.2 `site-scoped-query`：MODIFIED「ASIN 精确匹配覆盖三个商品槽位」为集合任意命中、去重、上限 100、不校验格式
- [x] 1.3 `.claude/rules/frontend.md` 中「ASIN 前端正则仅路由启发」改为「检索目标只由所选方式决定」

## 2. 后端（design D2、D3）

- [x] 2.1 `search_schemas.py`：`asin: str` → `asins: list[str]`，`_normalise_asins` 逐项规范化、保序去重，去重后 > `MAX_ASINS`(100) 时报 422，空集合视同 `None`
- [x] 2.2 `analysis_crud.py`：`_apply_filters` 三槽位 `IN (product_id 子查询)`；`request_has_filters` 改为 `req.asins`
- [x] 2.3 `analysis_service.py`：慢请求诊断摘要 `has_asin_filter` → `asins_count`（仍不含原文）
- [x] 2.4 测试：`test_analysis_filters_unit.py`（规范化、去重、非 B0、上限按去重后计数、周视图 SQL 三槽位）；`test_site_scoped_query_integration.py`（批量任意命中只出一行、未收录不影响、与排名筛选 AND）；`test_analysis_export_integration.py`（导出行与精确计数同口径）；`test_analysis_slow_query_unit.py`

## 3. 前端（design D1、D4、D5）

- [x] 3.1 `services/analysis.ts`：`SearchBody.asin` → `asins`
- [x] 3.2 `filters.ts`：`SearchMode`、`splitAsins`、`searchInputError`、`MAX_ASINS`；删除 `detectSearchKind`；`toSearchBody` 只按方式路由
- [x] 3.3 `ScopeBar.tsx/.less`：搜索方式为输入框前的独立 Select；ASIN 方式下显示个数、超限禁用提交与回车；粘贴时把换行和制表符转为空格
- [x] 3.4 `useAnalysisQuery.ts`：`FILTER_KEYS`、`submitScope`/`submitFilters` 改为 `asins`，`submitScope` 超限不提交；`stateCache.ts` 的 `CACHE_VERSION` 3→4
- [x] 3.5 测试：`filters.test.ts`、`ScopeBar.test.tsx`、`scopeAndFilters.test.tsx`、`defaultsAndFirstPaint.test.tsx` 已按新行为更新——**未由 Claude 运行**，由用户执行 `pnpm lint && pnpm tsc && pnpm test`

## 4. 验证

- [x] 4.1 后端：`uv run ruff check .` 通过。在 `keywordiq_test`（pgbase，经宿主 5433 端口）上运行定向 pytest（site_scoped_query、export、filters、slow_query、export_unit、request_logging、error_contract），133 passed。注：该库 alembic 版本为 v2 不认识的 `8f2a6c4e1b93`（他人分支的迁移），本变更无迁移，未处理
- [x] 4.2 性能：在本地 `keywordiq_exp148`（US 日报 2026-05-03 约 47 万行）上只读执行。精确计数：1 个 ASIN 185ms，100 个 ASIN 289ms；首页：1 个 148ms，100 个 48ms。计划为同一分区的 Bitmap 扫描加 hashed SubPlan，没有退化为嵌套循环
- [ ] 4.3 前端 lint/tsc/vitest——用户执行
- [ ] 4.4 浏览器实测与截图（两种方式下一行等高、菜单可选不截断、Excel 粘贴、101 个提示、三站点日/周）——用户执行，截图附 PR
