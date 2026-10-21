## 1. 证据盘点与前置核实

- [x] 1.1 周报文件名日期语义：用户确认为周结束日（周日起、周六止，如 `Week_2026_10_17`），周窗 `[w-6, w]`，已在 design.md 勾销
- [ ] 1.2 盘点每个站点 × 周期在库最早期次、缺期、保留下界、最早 `COMPLETED` 批次，结果写入本变更的 PR 说明（不入库）
- [x] 1.3 本机无项目测试库，另起独立容器 `pgbase`（postgres:15，映射 127.0.0.1:5433，库 `keywordiq_test`）；`pnpm install` 已触发

## 2. 数据模型与迁移

- [x] 2.1 `DimKeyword` 加 `first_seen_date`、`first_seen_period_type`、`first_seen_basis`（NOT NULL 默认 `UNKNOWN`）及列注释；登记索引 `idx_dim_keyword_first_seen (site, first_seen_date)`
- [x] 2.2 从 `FactDaily`/`FactWeekly` 删除 `is_new`；清理仍引用它的模型/索引
- [x] 2.3 手写 alembic 迁移（加列、建索引、删 `is_new`），`alembic upgrade head --sql` 核对分区 DDL 未丢，`test_models_match_migrated_schema` 绿
- [x] 2.4 实测 `CREATE INDEX`：300 万行 2.9s、索引 20MB（外推 1500 万行约 15s、100MB），迁移时短暂锁写 `dim_keyword`，选导入空闲时执行

## 3. 导入侧

- [x] 3.1 `importer` 补齐维度：新词条按 D2 写 `CONFIRMED`/`EARLIEST_OBSERVED`（基线期判定只读 `dim_keyword`）
- [x] 3.2 已存在词按 D3 比较键向前修正（`IS DISTINCT`/严格更小才写），与维度补齐同一事务
- [x] 3.3 `_load_facts` 停止计算/写入 `is_new`，删除 `NEW_WORD_LOOKBACK_DAYS`
- [x] 3.4 集成测试（真库）：重复导入、重试、日→周、周→日、同日并列、后补日报细化周报、乱序补更早、失败回滚、新站点首导、跨站点隔离、保留清理后不重置

## 4. 回填脚本

- [x] 4.1 `scripts/backfill_first_seen.py`：按站点/月分区批处理，用 D3 比较键，只写 `EARLIEST_OBSERVED`，`--dry-run`、`--sample N`，输出处理数/仍未知数/各依据计数
- [x] 4.2 脚本重复运行零更新的测试；永不产生 `CONFIRMED` 的测试
- [x] 4.3 删除 `scripts/backfill_is_new.py` 及其测试，清理文档引用

## 5. 查询层与接口

- [x] 5.1 `analysis_crud`：`first_listed` 筛选/排序/行投影改为对 `dim_keyword` 派生（日按日、周按 `[w-6,w]` 窗、仅 `CONFIRMED`），计入 `request_has_filters`（`or None`）
- [x] 5.2 `fact_metric_columns` 同步改为派生表达式（分析屏与词库盯盘共用，不复制第二份）
- [x] 5.3 `GET /api/analysis/first-listed` 返回数量与统计范围（含“不可判断”表达）；导出共用同一口径，列名改「首次入榜」
- [x] 5.4 请求/响应 schema 改名并加字段；`EXPLAIN` 实测（300 万词/30 万行日报）计数与筛选都走 `idx_dim_keyword_first_seen (site, first_seen_date)`，热缓存 0.3ms；补上了判定表达式里漏掉的 `site` 条件
- [x] 5.5 集成测试：日周映射例子（design D4 三个例子逐条）、未知/最早可观测不命中、监测起点“不可判断”、筛选总数 = `first_listed_count`

## 6. 前端

- [x] 6.1 `filters.ts`/`FilterPanel.tsx`/`columns.tsx`/`index.tsx`：「新词」→「首次入榜」，字段改 `first_listed`，默认值仍只一处定义，`FILTER_KEYS` 同步
- [x] 6.2 作用域区展示当期数量与说明文案；监测起点显示“无法判断”；周视图说明周精度
- [x] 6.3 `SAVED_MODE_VERSION` +1、`CACHE_VERSION` +1；对应测试
- [x] 6.4 词库盯盘复用分析屏的列与行字段，随之改名（Keyword 目录下无独立「新词」文案）；导出列名已在 5.3 改

## 7. 文档与收尾

- [x] 7.1 重写 `CLAUDE.md` 新词相关一节（口径、基线、日周映射、`UNKNOWN` 不可升 `CONFIRMED`）并同步 #119 引用
- [x] 7.2 本变更 `specs/` 与主 specs 一致性检查（`openspec validate`）
- [x] 7.3 完整 CSV 导入流程验证（`verify-import`，2026-10-21，独立库，US 真实报表 日 07-18/07-30、周 07-11/07-18，DE 日 07-16）：基线日报全部 `EARLIEST_OBSERVED`；新一天日报新词 `CONFIRMED`，接口数 = 黑名单口径下的 SQL 实算数（55860 / 总 64336）；日→周与乱序补更早周报只往前改日期（0 行后移、0 行降级、0 行升成 CONFIRMED）；同一期重复导入（日 07-18、07-30，周 07-11）首次记录摘要不变；DE 导入后 US 摘要不变；无重复关键词。**待验**：周报先于日报导入的次序未测
- [ ] 7.4 通知用户做市场分析页的实际浏览器验收（说明要看：默认只看首次入榜、数量文案、监测起点提示、日/周切换）；记录已通过与待验项目
- [ ] 7.5 PR 说明附：迁移/回填清单、处理数量、无法确认数量、抽样核验结果、已知未覆盖风险
