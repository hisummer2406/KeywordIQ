> 认领：2026-11-10，分支 `change/add-period-first-listing`，worktree `~/.claude-worktrees/add-period-first-listing`。Refs #178。
> **截止：2026-12-01 03:30 保留清理移出 9 月分区前须完成发布与生产回填（design Risks）。**

## 1. 规格与证据盘点

- [x] 1.1 proposal / design / 6 个 spec delta（keyword-first-seen、multi-site-data-model、site-scoped-import、site-scoped-query、analysis-screen-layout、keyword-trend-detail）
- [x] 1.2 生产只读盘点（2026-11-10）：三站日报 09-14～11-08 连续 56 期、周报 09-12～11-07 连续 9 期；事实最早期次 = 最早 COMPLETED 批次；无清理分区（design Context）
- [x] 1.3 `openspec validate --strict --all` 通过（41 项）；AGENTS.md 无首次证据表述无需同步；`.claude/rules/analysis.md` 两条首次证据约束补周期证据

## 2. 数据模型与迁移（D1、D2；multi-site-data-model「关键词维度保存首次入榜记录」）

- [x] 2.1 `DimKeyword` 加 `day_first_date/day_first_basis/week_first_date/week_first_basis` 与两个部分索引；列注释说明四种等级
- [x] 2.2 Alembic 迁移 `b7d3e9f2a6c1`（`schema="analysis"`，常量默认、建部分索引、downgrade）；2026-11-10 一次性库 `keywordiq_test_pfl` 上 `upgrade → downgrade -1 → upgrade` 通过，`test_models_match_migrated_schema` 通过
- [x] 2.3 测试：既有词两周期 UNKNOWN 且导入只推进为最早可观测（`test_period_first_integration.py::test_legacy_unknown_becomes_earliest_observed_per_period`）

## 3. 导入维护周期证据（D2；keyword-first-seen「周期首次证据在导入时维护，与顺序无关」、site-scoped-import）

- [x] 3.1 `importer._upsert_keywords`：新词写本周期 IMPORTED、另一周期 ABSENT；存量按表规则推进（相等不写）；SQL 抽成与回填共用的函数
- [x] 3.2 真 PG 测试：先周后日（#178 样例形态）、两日一周全排列、重复导入夹在中间、补更早日报、失败回滚、跨站点独立；共享记录逐行与改前相同。`test/test_period_first_integration.py`；反向变异（ABSENT→最早可观测、相等也写）对应用例失败
- [x] 3.3 真实报表验证（2026-11-10/11，一次性库 `keywordiq_verify_pfl_a/b`，经 `run_import` + LocalFileSource）：US 周 08-01/08-08（1.4G/1.2G）+ 日 08-02/03/09。A 按时间顺序、B 乱序且日 08-03 重复导入；11 个批次均 COMPLETED、total=written（2,730,758/2,316,515/451,823/426,011/452,662）；两库 2,904,950 个词的日/周/共享三组证据逐行相同（sha256 一致）。未覆盖：GB/DE、OSS 入口

## 4. 查询与 API（D3～D5；keyword-first-seen「首次进入当前榜单按周期独立派生」、site-scoped-query）

- [x] 4.1 `analysis_crud`：`period_imported_since_stmt`、`period_first_listed_expr`、`effective_first_listing(req)`；筛选/计数/行投影接入；`request_has_filters` 纳入有效口径
- [x] 4.2 请求模型 `first_listing` 与矛盾 422；行字段 `period_first_listed/period_first_date/period_first_basis`；`result_key` 规范化（旧参数与 MONITOR 同键）
- [x] 4.3 `GET /first-listed?mode=`：PERIOD 数量、不可判断、`previous_period_missing`（读 `import_batch`）
- [x] 4.4 导出：「首次入榜」后固定加「首次进入日榜/周榜」列（design D5 已改为两列固定都出）；慢请求摘要字段改为 `first_listing`
- [x] 4.5 测试：数量 = 列表总数（日/周、两口径）；周期不映射；`d ≤ B_P` 不可判断；矛盾 422（含导出开流前）；缓存键；缺期提示；旧调用响应不变。`test_period_first_integration.py`（查询部分）、`test_first_listing_mode_unit.py`、`test_result_key.py`、`test_site_scoped_query_integration.py::test_first_listed_summary_period_mode`
- [x] 4.6 本地真实规模（A 库，290 万词） `EXPLAIN (ANALYZE, BUFFERS)`：US 日视图与周视图 PERIOD 计数、`B_P`，确认走部分索引、无 dim_keyword 全表扫描。结果：`B_P` 为 `idx_dim_keyword_day_first` Index Only Scan，命中为同索引等值扫描；热缓存耗时 日 PERIOD 749ms（命中 4.5 万）/ MONITOR 7ms，周 PERIOD 2.6s / MONITOR 1.7s，随命中数增长。本地日报仅 3 天历史，日榜首次数（3.8～4.2 万/天）远大于生产预期

## 5. 回填脚本（D7；keyword-first-seen「周期首次证据可按证据重放回填」）

- [x] 5.1 `scripts/backfill_period_first.py`：复用 `evidence_gaps`、集合式重算、一条 UPDATE、`IS DISTINCT FROM`、`bak_period_first_<时间>`、`--dry-run`、`--undo`、`--sample`、导入锁
- [x] 5.2 真 PG 测试：逐期乱序导入结果 = 清空周期列后回填结果（B0 当期除外）；证据不完整拒绝且零修改；第二次零更新；撤销不误伤；共享记录不变；反向变异各让对应用例失败。`test/test_backfill_period_first_integration.py` 7 例；三处变异（`>` 改 `>=`、去掉证据校验、撤销不比对新值）各使对应用例失败

## 6. 前端（D6；analysis-screen-layout、keyword-trend-detail）

- [x] 6.1 `filters.ts` 草稿 `firstListing`、旧快照 `firstListed:true` 读为 MONITOR、`toSearchBody`、品牌排除随两种口径；不提升 `SAVED_MODE_VERSION`（读取时 `normalizeFirstListing`）；会话快照 `CACHE_VERSION` 4→5
- [x] 6.2 `FilterPanel` 三选一实心按钮组，PERIOD 文案随视图；`columns.tsx` 标签随口径；`FirstListedSummary` 标题、说明、缺期提示；「全部」选项值用小写 `all`，避免与行集合的 `ALL` 在测试按值定位时撞车
- [x] 6.3 趋势详情三项证据文案（后端 `KeywordTrendResponse.first_evidence` 已提供，`test_keyword_trend_integration.py::test_first_evidence_lists_day_week_and_monitor`）
- [x] 6.4 前端单测已改写/新增（`filters.test.ts`、`defaultsAndFirstPaint.test.tsx`、`columns.test.tsx`、`FirstListedSummary.test.tsx`、`savedMode.test.ts`）；提交门禁（ccf797e）实际运行 eslint、tsc、vitest 均通过

## 7. 交付与生产（各步单独授权）

- [ ] 7.1 后端 `uv run ruff check .` 与相关测试；PR `--base v2`，附 spec delta 列表与验证记录
- [ ] 7.2 发布（用户当次明确要求后触发 `Deploy V2 to ECS`）
- [ ] 7.3 生产 `--dry-run`：三站各周期等级计数、`B_P`、#178 四个样例的日榜/周榜证据
- [ ] 7.4 正式回填 + `VACUUM (ANALYZE) analysis.dim_keyword`（写操作，确认后执行）
- [ ] 7.5 #178 验收：四个样例在 10/31「首次进入日榜」命中、「首次进入监测」不命中；405 个旧版独有词逐条归类（命中 / 被其他条件排除 / 不可确认）；310 个新版独有词仍满足共享口径
- [ ] 7.6 浏览器人工验收（用户）：三选一控件、标签、数量、详情、导出、保存条件
