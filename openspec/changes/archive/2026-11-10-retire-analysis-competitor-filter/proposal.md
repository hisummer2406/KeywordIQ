## Why

#166 明确了竞对监控的业务边界：监控对象是店铺商品，市场分析只查 ABA 关键词，两者不做联动。现有市场分析「竞对新发现」筛选（`competitor_new`）读取用户私有的竞对关注与近期新发现 ASIN，让 ABA 查询依赖竞对模块的数据和权限。这与上述边界相冲突，还给结果键、权限校验和保存条件带来了专门的私有分支。#166 是本次的需求来源，它按「先 B 后 A」拆成两个变更，本变更是 B，竞对模块重构另开一个变更。

## What Changes

- **BREAKING** 下架市场分析的「竞对新发现」筛选：前端移除开关、草稿字段、请求组装及按权限规范化的逻辑；后端搜索、数量、导出三条路径都不再关联 `user_seller_watch` / `competitor_seller` / `competitor_seller_asin`。
- 旧参数有明确的退役处理：请求显式带 `competitor_new=true` 时返回 422，提示「竞对新发现筛选已下架」，不查询也不开流。`false` 或不传与其他请求等价，正常处理，旧客户端不会因此报错。
- 保存条件和会话快照里残留的 `competitorNew` 在读取时丢弃，不会重新施加，其余条件照常恢复。本次不提升 `SAVED_MODE_VERSION`，以免用户的有效保存条件被整份清掉。
- 移除只为这个筛选设置的权限依赖：`/search`、`/export` 不再按请求内容额外要求 `competitor:view`。通用的按需权限校验方法 `check_permission` 保留，只去掉以 `competitor_new` 为例的说明。
- 结果键不再有私有字段，所有分析请求都跨用户共享同一个键。「依赖私有数据的字段必须把用户标识纳入键」这条通用保护保留，私有字段登记表保持为空。
- `search` / `export` 链路上原先只供该筛选使用的 `user_id` 参数一并去掉。
- 退役 `competitor-keyword-crosslink` 整份规格，删除 `site-scoped-query` 中「竞对新发现是独立的筛选维度」这条 Requirement；同步修改规则、AGENTS 审核投影、导航文档和代码注释。
- 保留：ABA 前三商品、ASIN 批量查询、商品标题/品牌/类目筛选、事实表 `top{n}_product_id` 索引（ASIN 匹配仍依赖它）以及全部历史事实数据。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `site-scoped-query`：删除「竞对新发现是独立的筛选维度，按前三任一命中」；新增「已下架的竞对新发现参数明确拒绝」。
- `competitor-keyword-crosslink`：三条 Requirement 全部删除，整份能力退役。
- `analysis-query-coordination`：这份规格来自尚未归档的 `analysis-query-efficiency`，其中结果键的私有分类以 `competitor_new` 为例。它归档进 `openspec/specs/` 之后，再在本变更补上 MODIFIED delta（见 tasks 1.1）。在那之前本变更不写这份 delta，也不修改另一个变更的目录。

## Impact

- 路径：日报、周报两个视图都受影响。它们共用 `analysis_crud._apply_filters`、计数和导出，删除的是同一个分支。
- 后端：`app/table/search/search_schemas.py`、`app/table/analysis/{analysis_api,analysis_service,analysis_crud,result_key,fact_model}.py`、`app/auth/simple_auth.py`（仅注释）。
- 前端：`frontend/src/pages/Analysis/{filters.ts,FilterPanel.tsx,useAnalysisQuery.ts,savedMode.ts,index.tsx}`、`frontend/src/services/analysis.ts` 及对应测试。
- 测试：`test/test_analysis_competitor_permission_unit.py` 改写为退役用例；同步修改 `test_analysis_filters_unit`、`test_analysis_slow_query_unit`、`test_result_key`、`test_site_scoped_query_integration`、`test_analysis_export_unit`。
- 文档与规则：`.claude/rules/{frontend,auth}.md`、`AGENTS.md`（Code Review Rules 的权限放宽条款）、`openspec/guides/{competitor,frontend}.md`。
- 不涉及数据库结构或迁移，不改竞对模块的表与接口，不影响 V1。
