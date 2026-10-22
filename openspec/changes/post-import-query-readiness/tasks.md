> 进度记录：2026-10-22 Claude（Opus）在 `change/post-import-query-readiness`（worktree `~/.claude-worktrees/post-import-query-readiness`）开工；实验库 `keywordiq_exp148`（pgbase，本变更专用，勿用于共享测试）。

## 0. 根因实验（design「实验证据」）

- [x] 0.1 真实 US 日报 05-03、05-04 导入一次性库，叶子分区关闭 autovacuum 控制统计状态，用 `analysis_crud` 语句构造函数生成 SQL 做 `EXPLAIN ANALYZE`。结论：新期次未进统计时估 1 行，查询 13–31 s，首次入榜筛选 >120 s；仅 ANALYZE 日报叶子（0.65 s）后全部 ≤1.3 s。周报无样本，未测。

## 1. 导入后刷新查询统计（site-scoped-import「新期次可见前刷新查询统计」；design D1）

- [ ] 1.1 `partitioning` 提供「本次导入写入的叶子分区名」：日/周站点分区，以及当月掉榜、增长分区
- [ ] 1.2 import service 新增刷新函数：`SET LOCAL statement_timeout` + 逐表 `ANALYZE`；新增配置 `IMPORT_ANALYZE_TIMEOUT_SECONDS`，同步 `.env.example`
- [ ] 1.3 `_execute_locked`：事实提交后、`_update_site_data_stats` 前调用；日/周/重导同路径
- [ ] 1.4 真库测试：导入后叶子分区 `last_analyze` 非空且早于 site_data_stats 写入时间；日、周、重导各一例

## 2. 统计状态与自动恢复（site-scoped-import「查询统计刷新失败可见并自动恢复」；design D2）

- [ ] 2.1 `ImportBatch` 加 `stats_status`、`stats_attempts`；手写 Alembic 迁移，一次性库验证 upgrade → downgrade → upgrade，并核对模型差异
- [ ] 2.2 刷新成功置 READY；失败 attempts+1、记 `logger.exception`，批次仍置 COMPLETED，期次仍可见
- [ ] 2.3 导入轮询新增维护性 job：非阻塞拿 `import_lock`，重试 PENDING，第 3 次失败置 FAILED；拿不到锁跳过
- [ ] 2.4 测试：刷新失败时数据可查且状态为 PENDING；空闲重试成功转 READY；上限后转 FAILED；持锁时跳过
- [ ] 2.5 `ImportBatchRow` 返回 `stats_status`（import-history-view「批次展示查询统计状态」）
- [ ] 2.6 前端 `ImportStatusCell`：COMPLETED + PENDING/FAILED 时显示说明；补组件测试（前端 lint/tsc/test 由用户运行）

## 3. 交互查询执行超时（async-request-execution「交互查询有执行时间预算」；api-error-contract 503；design D3）

- [ ] 3.1 配置 `INTERACTIVE_STATEMENT_TIMEOUT_MS`（默认 30000），写入异步引擎 `server_settings`，同步 `.env.example`
- [ ] 3.2 错误处理：SQLSTATE 57014 → 503 `db.query_timeout`，WARNING 不打栈，不泄漏 SQL；登记错误码常量
- [ ] 3.3 真库测试：异步会话执行 `pg_sleep` 超过预算后返回 503 与对应 code，连接可复用；同步引擎同样语句不被取消

## 4. 分析查询并发上限（async-request-execution「分析查询并发有上限且排队有界」；design D4）

- [ ] 4.1 配置 `ANALYSIS_MAX_CONCURRENT_SEARCHES=4`、`ANALYSIS_SEARCH_QUEUE_TIMEOUT_SECONDS=5`，同步 `.env.example`
- [ ] 4.2 `search` 外包进程级信号量，等待超时抛 503 `analysis.busy`
- [ ] 4.3 测试：占满时第 N+1 个请求超时返回 busy；释放后可继续；其他端点不受信号量影响

## 5. 验证与交付

- [ ] 5.1 `uv run ruff check .`；后端定向测试；全量 pytest 交给门禁/CI
- [ ] 5.2 verify-import：在 `keywordiq_exp148` 用真实日报回放，确认导入后无需手工维护即可快速查询，记录前后耗时
- [ ] 5.3 `openspec validate --strict --all`
- [ ] 5.4 PR（base v2，Refs #148），列出 spec delta 与实际验证；浏览器验收交给用户（导入历史提示、查询超时文案）
- [ ] 5.5 #148「后续」部分另开 Issue（去重/取消/缓存/计数优化/分阶段预算）
