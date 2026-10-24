> 进度记录：2026-10-22 Claude（Opus）在 `change/post-import-query-readiness`（worktree `~/.claude-worktrees/post-import-query-readiness`）开工；实验库 `keywordiq_exp148`（pgbase，本变更专用，勿用于共享测试）。

## 0. 根因实验（design「实验证据」）

- [x] 0.1 真实 US 日报 05-03、05-04 导入一次性库，叶子分区关闭 autovacuum 控制统计状态，用 `analysis_crud` 语句构造函数生成 SQL 做 `EXPLAIN ANALYZE`。结论：新期次未进统计时估 1 行，查询 13–31 s，首次入榜筛选 >120 s；仅 ANALYZE 日报叶子（0.65 s）后全部 ≤1.3 s。周报无样本，未测。

## 1. 导入后刷新查询统计（site-scoped-import「新期次可见前刷新查询统计」；design D1）

- [x] 1.1 `partitioning.leaf_partition_name` + `importer.written_fact_tables/written_leaf_partitions`；建分区与刷新共用同一份表清单
- [x] 1.2 `import_service.refresh_query_stats`：`set_config('statement_timeout', …, true)` + 逐叶子 `ANALYZE`；`IMPORT_ANALYZE_TIMEOUT_SECONDS` 已加入 config/.env.example
- [x] 1.3 `_execute_locked`：事实与 PENDING 同事务提交 → 刷新 → `_update_site_data_stats`
- [x] 1.4 `test_import_service_integration.py`：写 site_data_stats 时统计已含新期（spy）；周报叶子+掉榜已分析、重导 READY；去掉刷新调用后 3 例失败（反向验证）

## 2. 统计状态与自动恢复（site-scoped-import「查询统计刷新失败可见并自动恢复」；design D2）

- [x] 2.1 迁移 `8f2a6c4e1b93`；`keywordiq_exp148` 上 upgrade→downgrade→upgrade 通过，`alembic check` 无差异；`keywordiq_test` 已升到 head
- [x] 2.2 `_refresh_batch_query_stats`；收尾失败的批次 stats_status 清为 NULL
- [x] 2.3 `retry_pending_query_stats`（每轮 ≤10 批）+ `import_queue.retry_query_stats_round`，排在 drain 之后
- [x] 2.4 四例均在 `test_import_service_integration.py`
- [x] 2.5 `ImportBatchRow` 与最近导入看板均返回 `stats_status`；`test_import_history_api.py` 覆盖
- [ ] 2.6 前端 `ImportStatusCell` + 两屏传参 + `Upload/index.test.tsx` 用例已写；**前端 lint/tsc/test 未运行（由用户运行）**

## 3. 交互查询执行超时（async-request-execution「交互查询有执行时间预算」；api-error-contract 503；design D3）

- [x] 3.1 `database.async_engine` server_settings；正数校验（0 在 PG 是不限时）
- [x] 3.2 实测 asyncpg 超时经 SQLAlchemy 包装为 `DBAPIError`（非 OperationalError，原先会落到 500 `db.error`）；按 `orig.sqlstate/pgcode == 57014` 识别；`test_error_contract_unit.py` 覆盖两种驱动形态
- [x] 3.3 `test_interactive_query_budget.py`：异步引擎 statement_timeout=配置值、同步引擎为 0；超时 <2 s 被取消、连接可复用、服务端无残留

## 4. 分析查询并发上限（async-request-execution「分析查询并发有上限且排队有界」；design D4）

- [x] 4.1 已加入 config/.env.example
- [x] 4.2 `analysis_service.search_slot`（按事件循环惰性建闸；用 `asyncio.timeout` 避开 3.11 `wait_for` 丢许可）；API 层 `async with`
- [x] 4.3 占满返回 busy、超时内释放可进入、异常释放许可（单元）；其他端点不经过闸（结构上只包 search）

## 5. 验证与交付

- [x] 5.1 ruff 通过；定向 26 个文件 + 新文件真库回归 366 passed / 0 skipped（keywordiq_test@pgbase:5433）；全量由门禁/CI
- [x] 5.2 新代码导入真实 05-06（42.5 万行，叶子 autovacuum 仍关闭），批次 READY、耗时 60 s；未做任何手工维护即：默认 0.23 s、排除品牌词 0.99 s、首次入榜 0.94 s（R2 同场景旧代码为 18 s / 31 s / >120 s）。周报无真实样本，仅单测覆盖
- [x] 5.3 `openspec validate --strict --all`：40 passed
- [ ] 5.4 PR（base v2，Refs #148），列出 spec delta 与实际验证；浏览器验收交给用户（导入历史提示、查询超时文案）
- [ ] 5.5 #148「后续」部分另开 Issue（去重/取消/缓存/计数优化/分阶段预算）

## 6. PR #149 Codex 复审修复（2026-10-24 Claude Opus）

- [x] 6.1 P1 排队者攥着鉴权连接（async-request-execution「排队中的分析查询不占连接」；design D4）：`search_slot(db)` 排队前 `close()` 请求会话；`test_waiting_for_a_slot_does_not_hold_a_pool_connection` 真库断言排队期间池占用为 0；去掉 `close()` 后该例失败
- [x] 6.2 P2 收尾中途退出卡在 PROCESSING（site-scoped-import「收尾中途退出后自动补完」「未提交事实的中断批次不被补完」；design D2「中途退出」）：计数随事实落批次，`_finish_committed_import` 供正常收尾与 `_resume_interrupted_finish` 共用；`test_import_service_integration.py` 三例（两个中断点补完、补完再失败判 FAILED、无事实的 PROCESSING 不碰）；去掉补完调用后 3 例失败
- [x] 6.3 真库（keywordiq_test@pgbase:5433）跑 `test/` 全部：1307 passed / 1 skipped（OSS 实链）/ 3 failed → 其中 2 例是 `test_analysis_competitor_permission_unit.py` 的会话替身为 None，已改替身后 4 passed；剩 `test_v2_integration::test_models_match_migrated_schema` 因共享测试库被其他分支迁移改过（缺 `is_new` 列、`first_seen_basis` 注释不同），与本变更无关
- [x] 6.4 第二轮复审两条 P2（重导后恢复用旧行数覆盖 site_data_stats；保留清理移除后恢复把期次重新发布）：`importer.committed_slice_counts` 取切片现状，切片已空且批次写过行时判 FAILED 不发布（site-scoped-import「补完时按切片现状发布」；design D2）。新增两例，去掉修复后两例失败；导入/上传/保留/OSS 定向真库回归通过
- [x] 6.5 事实提交前中断的 PROCESSING 批次（复审汇总中列为既有问题，用户要求一并处理）：恢复 job 持锁时判 FAILED，可重导、不再挡 ops-bot（site-scoped-import「未提交事实的中断批次判为失败」；design D2）。`test_import_interrupted_before_facts_commit_is_marked_failed`（去掉判失败后失败）+ 持锁时不碰真在跑的 PROCESSING。**部署后迁移前遗留的 PROCESSING 批次也会被判失败**，生产是否存在此类批次未核对

## 7. 第一阶段剩余验收

- [x] 7.1 生产只读基线（用户授权，2026-10-24）与性能预算写入 design「生产基线」「性能预算」：修复前 search p95 1343 s，10-22 首次入榜 + 排除品牌词单请求最长 7884 s，轻量接口 p99 30 s；生产无遗留 PROCESSING 批次
- [x] 7.2 完整请求预算（async-request-execution「完整请求预算到点由数据库停止执行」；design D3「完整请求预算」）：`ANALYSIS_REQUEST_TIMEOUT_SECONDS=25`（config/.env.example），API 排队前取截止时刻，`_timed_execute` 逐条 `SET LOCAL statement_timeout` 到剩余预算，用尽抛 `AnalysisTimeoutError`。先实测 asyncio 取消不停服务端语句（pg_sleep(8) 跑满 8 s）。`test_interactive_query_budget.py` 新增三例，真库验证数据库取消、无残留、连接可复用；去掉收紧后两例失败。定向回归 128 passed，另有 1 例失败（`test_models_match_migrated_schema`，共享测试库被其他分支迁移改过，与本变更无关）
- [x] 7.3 连续两期回归（日报，design「连续两期回归」）：`keywordiq_exp148b` 依次导入 4 期真实 US 日报作为历史，关闭 5 月叶子分区 autovacuum，服务不重启，连续导入 05-04、05-06。每期就绪后立即查询，全程不手工 ANALYZE：默认列表首次 552 / 458 ms，首次入榜 + 排除品牌词首次 2569 / 2139 ms，计划估算正确。生产规模约为本地的 10 倍，这次只证明机制；生产是否达标以 7.5 重采为准
- [ ] 7.4 周报：本机无样本，用户决定暂缓；部署后按同口径采集，需先让请求摘要能区分视图
- [ ] 7.5 部署后按 design「性能预算」口径重采，对比基线
