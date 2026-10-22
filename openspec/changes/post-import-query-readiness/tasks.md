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
