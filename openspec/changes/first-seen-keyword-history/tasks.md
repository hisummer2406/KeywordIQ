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
- [ ] 7.3 完整 CSV 导入流程验证（`verify-import`）：**须在第 8 节口径修订实现后重跑**。2026-10-21 的一次通过（独立库，US 日 07-18/07-30、周 07-11/07-18，DE 日 07-16）跑在旧口径（导入时打 CONFIRMED/EARLIEST_OBSERVED 标签）上，其中“只往前改、重复导入摘要不变、跨站点隔离”的结论对新口径仍成立，但首次入榜数量需重测
- [ ] 7.4 通知用户做市场分析页的实际浏览器验收（说明要看：默认只看首次入榜、数量文案、监测起点提示、日/周切换）；记录已通过与待验项目
- [ ] 7.5 PR 说明附：迁移/回填清单、处理数量、无法确认数量、抽样核验结果、已知未覆盖风险

## 8. 口径修订：首次入榜按最终数据派生，与导入顺序无关（PR #141 Codex P1，2026-10-21）

用户要求：选哪天，哪天的首次入榜要正确，与导入顺序无关。设计 D2 已改写（见 design.md）。取值 `CONFIRMED` 改名 `IMPORTED`；本变更未上线，直接改同一条迁移，无数据迁移。

- [ ] 8.1 模型与迁移：`first_seen_basis` 取值/列注释改为 `IMPORTED / EARLIEST_OBSERVED / UNKNOWN`（改 `3c8e5b1d7f42`，`test_models_match_migrated_schema` 绿）；全仓 `CONFIRMED` 引用同步（后端、前端、脚本、测试、CLAUDE.md）
- [ ] 8.2 `importer`：新词条一律记 `IMPORTED`，删除“基线期”分流与对 `min(first_seen_date)` 的读取；`UNKNOWN → EARLIEST_OBSERVED` 与向前修正（D3）不变
- [ ] 8.3 `analysis_crud`：站点监测起点 `B = min(first_seen_date) WHERE basis='IMPORTED'`（回填不移动 B）；`first_listed_expr` 加 `first_seen_date > B` 且来源 `IMPORTED`；不可判断（日 `d ≤ B`、周 `w-6 ≤ B`、`B` 为空）时筛选不命中任何词；`confirmed_since_stmt` 改为取 `B`；`fact_metric_columns` 同步（分析屏与词库盯盘共用）
- [ ] 8.4 `analysis_service.get_first_listed`：`judgeable` 按 D4 的日/周条件；`earliest_known_date` 返回 `B`
- [ ] 8.5 回填脚本：文案/断言按新取值；仍永不写 `IMPORTED`；补测试
- [ ] 8.6 **顺序无关性测试（真库，验收核心）**：同站点 3 份日报全部 6 种导入顺序，逐行比较 `dim_keyword` 并比较每一天首次入榜数量；2 份日报 + 1 份覆盖它们的周报全部顺序；乱序中途夹一次重复导入；补更早报告后先到的基线词（design D4 例子里的词 E）出现在日视图首次入榜
- [ ] 8.7 前端：不可判断文案按“不晚于监测起点”；标签仅在 `first_listed` 为真时出现（已满足，核对）；`CACHE_VERSION` / `SAVED_MODE_VERSION` 按字段形状变化决定是否再 +1
- [ ] 8.8 `CLAUDE.md` 首次入榜一节、PR #141 说明按新口径重写
- [ ] 8.9 重跑 7.3（真实 CSV，含乱序：先周 07-18、后日 07-18、再补更早周 07-11，对比顺序导入的结果）
- [ ] 8.10 部署兼容（PR #141 Codex P1-2，expand/contract）：`is_new` 已随 2026-10-19 部署上线，本次迁移**不删列、不删其部分索引**，只给 `is_new` 加 DB 侧 `server_default false`（旧后端窗口内仍可读写，回滚可用）；新代码不再读写它；模型与 `test_models_match_migrated_schema` 为该列做带注释的例外；下个版本单独变更删列，需开 issue 登记
