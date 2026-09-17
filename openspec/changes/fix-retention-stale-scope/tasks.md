## 1. 认领与环境

- [x] 1.1 认领分支 `change/fix-retention-stale-scope`，worktree 建在
      `~/.claude-worktrees/fix-retention-stale-scope`（v2 分支）。
- [x] 1.2 确认 `keywordiq_test` 可用（本地集成测试与门禁的共享库）。

## 2. 保留清理：清掉残留统计

- [x] 2.1 `app/table/analysis/retention_service.py` 新增 `purge_stale_stats()`，
      不提交事务，对 `site_data_stats`／`category_stats`／`brand_stats` 三张表按
      `report_date < cutoff` 统计（始终执行）与清理（非 dry_run 才执行）。
- [x] 2.2 `RetentionResult` 新增 `purged_stats` 字段与 `total_purged_stats` 属性，
      记录三张表各自被清理（或将被清理）的行数，供脚本日志与测试断言使用。
- [x] 2.3 `run_retention` 在分区 DROP 循环之后、commit/rollback 之前调用
      `purge_stale_stats`，无条件执行（不判断本次是否 DROP 了分区）。
- [x] 2.4 `logger.info` 的结尾汇总补上清理的统计行数。

## 3. 运维脚本

- [x] 3.1 `scripts/prune_retention.py`：早退判断改为
      `total_dropped == 0 and total_purged_stats == 0` 才真正退出；
      dry-run 预览日志、确认提示与完成日志都补上每张统计表清理的行数。

## 4. 测试

- [x] 4.1 `test/test_retention_integration.py` 新增用例（复用现有 `conn` fixture，
      直接调用 `purge_stale_stats`，覆盖）：
      - 正常场景：三张统计表超期行被清理，窗口内行不受影响
        （`test_purge_stale_stats_removes_expired_rows_keeps_in_window`）。
      - 历史残留：不建对应分区（模拟"此前已被移出"），只插入残留统计行，验证仍被清理
        （`test_purge_stale_stats_removes_historical_residue_without_matching_partition`）。
      - dry-run：只统计不写入（`test_purge_stale_stats_dry_run_does_not_delete`）。
      - 幂等：清理两次，第二次清理不到任何行（`test_purge_stale_stats_is_idempotent`）。
- [x] 4.2 新增 `test_run_retention_purges_stats_in_the_same_run_as_partition_drop`，
      复现 issue #100 原始步骤：写入超期事实行 + `site_data_stats`，跑一次真实
      `run_retention`，断言 `analysis_crud.available_dates_stmt` 不再包含该日期、
      `scope_row_count_stmt` 对该日期返回空。用
      `Session(bind=conn, join_transaction_mode="create_savepoint")` 包一层——
      `run_retention` 内部会真的 `commit()`，直接绑到共享 `conn` fixture 会提交整个
      事务、冲掉夹具"结束即回滚、零残留"的前提（design.md 决策 5，已用一次性脚本
      对 `keywordiq_test` 核对测试跑完后无残留）。
- [x] 4.3 已跑 `test_retention_unit.py`、`test_retention_integration.py`（14 用例）、
      `test_keyword_trend_unit.py`、`test_keyword_trend_integration.py`、
      `test_site_scoped_query_integration.py`、`growth`/`library` 相关用例、以及
      带 `TEST_DATABASE_URL` 的全量 `pytest test/`（1005 passed, 2 skipped），
      均通过；`test_floor_for_range_all_*` 系列不受影响（design.md 决策 1）。

## 5. 收尾

- [x] 5.1 更新 CLAUDE.md「趋势详情」一节与「数据保留」一节，把"从不清理
      `site_data_stats`——既有代码一个尚未触发问题的缺口，本变更不修"改为指向
      本变更已修复，同时保留 `trend_service` 原有防御性判断作为双重保险的说明。
- [x] 5.2 `ruff check .` 全量通过；定向 + 全量 pytest（含 `TEST_DATABASE_URL`）通过。
- [ ] 5.3 提交、开 PR（`--base v2`），链接 issue #100，附复现前后对比与测试结果。

Refs #100
