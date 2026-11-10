> 来源：#166 第四节。本变更只做市场分析解耦；竞对模块重构另开变更。
> 进度留痕：2026-11-04 提案并实现（Claude Code，worktree `~/.claude-worktrees/retire-analysis-competitor-filter`）。已提交 `088cb58` 并通过门禁；浏览器验收待用户执行。

## 1. 前置与依赖

- [x] 1.1 `analysis-query-efficiency` 已于 2026-11-04 归档（`2558ea1`），本分支已快进到该提交。已补 `specs/analysis-query-coordination/spec.md` 的 MODIFIED delta：私有字段改为通用表述，并说明当前登记表为空；两个以 `competitor_new` 为条件的 Scenario 换成「当前所有请求跨用户共享」「已退役参数取假值不改变结果键」（design D6）
- [x] 1.2 实现前已 grep `competitor_new|competitorNew|COMPETITOR_VIEW`，调用点与 proposal Impact 一致；竞对模块自身端点使用 `COMPETITOR_VIEW` 属于正常用途，不改。实现中另发现 `etc/observability-runbook.md` 的诊断字段清单也列了该字段，已一并删除

## 2. 后端：退役参数（site-scoped-query「已下架的竞对新发现参数明确拒绝」，design D1）

- [x] 2.1 `search_schemas.AnalysisSearchRequest.competitor_new` 新增校验器 `_reject_retired_competitor_new`：`True` 返回 422「竞对新发现筛选已下架，请移除该条件」；`False`/`None` 一律落回 `None`；字段描述改为退役说明
- [x] 2.2 `analysis_api` 的 `/search`、`/export` 删除按请求内容执行的 `competitor:view` 校验及 `COMPETITOR_VIEW` import；`simple_auth.check_permission` 保留（目前没有调用方，design D2/D3），说明中的例子改为通用表述
- [x] 2.3 `test/test_analysis_competitor_permission_unit.py` 改名为 `test_analysis_competitor_new_retired_unit.py`，8 例：日/周 × `/search`、`/export` 带 `true` 均返回 422，即使拥有全部权限；service 桩一旦被调用即失败，导出响应不带附件头（证明未开流）；只有 `analysis:view` 的用户传 `false`/`null` 正常返回 200，到达 service 时字段为 `None`；只有 `analysis:export` 的用户传 `false` 正常导出；模型层 `false` 归一为 `None`

## 3. 后端：移除竞对关联（design D2、D3）

- [x] 3.1 `analysis_crud`：删除竞对子查询和四个竞对模块 import；`request_has_filters` 去掉 `competitor_new`（说明改为两态布尔的通用规则）；`_apply_filters`、`search_stmt`、`count_capped_stmt`、`count_filtered_stmt`、`export_stmt` 及同步包装函数都去掉 `user_id`
- [x] 3.2 `analysis_service`：`search`、`_listed_total`、`resolve_export_scope`、`iter_export_csv`、`search_summary_fields` 去掉 `user_id`；`_search_filter_summary` 去掉 `competitor_new`；API 鉴权依赖不变（形参改名为 `_user`）
- [x] 3.3 `result_key`：`_PRIVATE_FLAGS` 改为空字典 `{字段: 权限点}`；`user_id` 改为可选参数，命中私有字段却没给 `user_id` 时抛错；`competitor_new` 仍归为 `_FLAG`，并加注释说明；模块文档已改写
- [x] 3.4 `fact_model` 中 `top{n}_product_id` 索引的注释删除「竞对新发现」用途，索引本身不动
- [x] 3.5 单元测试：`test_result_key.py` 删除按用户区分 `competitor_new` 的用例，新增三例：登记表为空且 `false` 与不传同键、同 scope；登记机制保留（登记一个私有字段后按用户和权限区分）；私有字段缺 `user_id` 时拒绝。`test_analysis_filters_unit.py` 三个竞对用例合并为一例：`false`/`None` 的 SQL 不出现竞对表，且不计为带筛选。`test_analysis_slow_query_unit.py`、`test_analysis_export_unit.py`、`test_hidden_categories_unit.py` 只删除 `user_id` 参数。`uv run pytest test/test_result_key.py test/test_analysis_filters_unit.py test/test_analysis_slow_query_unit.py test/test_analysis_competitor_new_retired_unit.py -q` 结果 41 passed
- [x] 3.6 真库回归（2026-11-04，pgbase@5433 `keywordiq_test`，迁移 head `d7a3b5c9e2f1`，运行前活动连接数为 0）：`test_site_scoped_query_integration.py` 删除 9 个竞对命中用例，保留 `_seed_competitor_watch` 种入真实的关注和近期新发现数据，新增 `test_retired_competitor_new_false_matches_omitted`（日、周各一次，带 `false` 与不带时的 total、行序完全一致，且 total>0）。分析相关的单元和集成测试（`test_analysis*`、`test_result_key`、site_scoped、brand_hits、first_seen、window_scoped_rows、hidden_categories、growth_query、retention、import_history、verify_selfhosted_migration、cancel_on_disconnect）共 230 passed，0 skipped。首轮暴露 `test_first_seen_integration` 有一处位置参数漏改，报 TypeError（不会错位静默通过），已修复

## 4. 前端（design D4）

- [x] 4.1 `filters.ts` 删除 `competitorNew`、`sanitizeCompetitorNew` 和 `toSearchBody` 中的 `competitor_new`；`services/analysis.ts` 删除该字段类型
- [x] 4.2 `FilterPanel.tsx` 删除开关、`COMPETITOR_NEW_HINT` 和 `canCompetitorNew` 属性；`index.tsx` 不再传 `access.canViewCompetitor`；`useAnalysisQuery.ts` 的 `FILTER_KEYS` 删除 `competitor_new`，删除 `canViewCompetitor` 参数和 `apply` 中的权限兜底
- [x] 4.3 `savedMode.ts` 新增 `RETIRED_FILTER_KEYS` / `dropRetiredFilters`，`readSavedMode` 读取时剔除；`SAVED_MODE_VERSION` 不变。`savedMode.test.ts` 新增用例：旧 payload 带 `competitorNew: true`，读出结果与不带该字段的完全相等
- [x] 4.4 已修改 `filters.test.ts`（新增「请求体不带 competitor_new」，删除 sanitize 用例）、`scopeAndFilters.test.tsx`（三例合并为「有 competitor:view 也没有开关、首屏不带该字段」）、`defaultsAndFirstPaint.test.tsx`（权限收回用例改为「旧保存条件开着它时其余条件照常恢复」）。提交门禁在 `088cb58` 上运行 `pnpm lint && pnpm tsc && pnpm test`，全部通过。首次提交时 tsc 报出：删除 `sanitizeCompetitorNew` 后，恢复保存条件处的 `qMode: 'ALL_TERMS'` 被拓宽为 `string`，已改为 `as const`

## 5. 规则与文档

- [x] 5.1 `.claude/rules/frontend.md`：删除 competitorNew 规范化那一条；「改形状提升版本」那条补上「仅删除字段时在读取处剔除」（来源 D4），并更正版本常量的实际位置（`stateCache.ts`、`savedMode.ts`，原文误写为 `index.tsx`）
- [x] 5.2 `.claude/rules/auth.md` 与 `AGENTS.md` 的跨域放宽条款：通用纪律保留，例子改为「原例 competitor_new 已于 #166 下架」；AGENTS Code Review Rules 的语义没有变化。`.claude/rules/competitor.md` 中「联动筛选规格」改为「采集端规格」
- [x] 5.3 `openspec/guides/{competitor,frontend}.md` 删除 `competitor-keyword-crosslink` 链接；`etc/observability-runbook.md` 的诊断字段清单删除该字段；`docs/product-monitoring-risk` 不改（那里指竞对数据来源，不是本筛选）

## 6. 验收

- [x] 6.1 提交门禁在 `088cb58` 上执行 `uv run ruff check .`、`uv run pytest test/ -q`（`TEST_DATABASE_URL` 指向 5433 的 `keywordiq_test`），并执行前端三项检查，全部通过。首次提交被拦下两类问题：`test_interactive_query_budget.py` 有一处按位置传 `user_id`，报 TypeError，已修；另有 7 例连接失败，原因是 worktree 的 `.env` 复制自主仓、指向 5432 的其他实例，改指 5433 后通过。这属于环境问题，与本次代码无关。另手工运行 `openspec validate --strict --all`（40 项）与 `git diff --check`，均通过
- [ ] 6.2 浏览器验收交给用户：分析查询屏没有「竞对新发现」开关；已保存的条件恢复后其余项不变；日/周视图的查询、数量和导出正常
- [ ] 6.3 归档时：如果 `competitor-keyword-crosslink` 留下空规格，按 design D5 删除该目录
