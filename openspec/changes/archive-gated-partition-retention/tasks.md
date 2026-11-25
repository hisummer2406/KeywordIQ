## 1. 归档闸门（multi-site-data-model「数据按月归档就绪」）

- [x] 1.1 在 `retention_service` 增加期次枚举：按月分区的实际站点与日历日做 EXISTS 点查，覆盖日报与周报（design D2、D3）。实现：`_periods_with_data`、`partitioning.list_site_subpartitions`；生产只读 EXPLAIN 为 Index Only Scan
- [x] 1.2 增加单期次校验：最新 COMPLETED 批次、object key、写入行数、对象存在与大小，输出 D4 的失败原因（Scenario: 同一期次多次导入以最新成功批次为准）。实现：`verify_month_archive`
- [x] 1.3 接入 `run_retention`：校验在咨询锁内、先于所有 DROP，按月整体拦截，`RetentionResult` 增加 blocked 明细，dry-run 同样校验且不取锁（D1、D2、D5）
- [x] 1.4 OSS 未启用或非 404 错误时 fail closed（Scenario: 对象存储不可用时不移出）。实现：`_default_archive_stat`，出错即停止校验该月
- [x] 1.5 校验分支测试：通过、缺批次、空 key、0 行、对象缺失、空对象、OSS 异常。归入真实库用例 `uv run pytest test/test_retention_archive_gate_integration.py -q`（需 `TEST_DATABASE_URL`）
- [x] 1.6 真实库集成测试：拦截月全部保留（日报、周报、掉榜）、其他月正常移出、dry-run 不写入、周报期次受校验，`test/test_retention_archive_gate_integration.py`；既有 `test_retention_integration.py` 的端到端用例已补批次

## 2. CLI

- [x] 2.1 `prune_retention.py` 增加 `--json`（stdout 最后一行结果），日志展示被拦截月份，退出码约定写入帮助（D7）
- [x] 2.2 用集成测试复现 `--export-dir` 在带子分区月分区上的行为（已复现 `cannot copy from partitioned table`），改为 `COPY (SELECT ...)`，覆盖「移出前可完整导出」（D10）

## 3. 运维定时任务（ops-monitoring-alerting「定时保留清理与告警」）

- [x] 3.1 `config.py` 与 `.env.example` 增加 `OPS_RETENTION_MODE`（off/dry_run/execute，默认 off，非法值启动报错）（D9）
- [x] 3.2 新增 `ops_bot/retention.py`：`asyncio.to_thread` 中运行 `docker compose run --rm app python scripts/prune_retention.py --json`，超时 30 分钟，解析结果，不 import `app`（D7）
- [x] 3.3 接入 `build_jobs`：`RETENTION_PRUNE`，每日 03:30，仅非 off 注册；execute 先 dry-run 留痕再执行（D9）
- [x] 3.4 告警 `retention-gate-blocked`、`retention-job-failed`，失败不抛出以避免每分钟重跑，lock_skipped 不告警，明细上限 20 条（D8、D9）
- [x] 3.5 单元测试：三种模式、拦截告警、失败不重跑、超时、JSON 损坏、lock_skipped，`uv run pytest test/test_ops_bot_retention_unit.py -q`

## 4. 文档与校验

- [x] 4.1 更新 `etc/keywordiq-ops-bot-runbook.md`：运行模式、告警处理、原始对象不得配置过期规则
- [x] 4.2 提交门禁在本地 `keywordiq_test` 上运行全量后端检查并通过（2026-11-09，提交 4b78420）；按用户决定不开 PR，直接合并进 v2，合并后由 v2 的 CI 复核

## 5. 上线（需用户分别授权）

- [ ] 5.1 复核生产 root crontab 没有保留清理任务（需要 root 权限）
- [ ] 5.2 发布后配置 `OPS_RETENTION_MODE=dry_run` 并重启 ops-bot，观察至少 2 天，用 `--retention-months 2` dry-run 验证 9 月闸门结果
- [ ] 5.3 2026-12-01 前用户确认后切换 `execute`
- [ ] 5.4 确认首次运行后 9 月分区已移出、OSS 对象仍在、磁盘占用下降

## 6. 写入批次判定修正（#209 P1）

- [x] 6.1 迁移 `b7d2e9a4c1f6` 增加 `import_batch.facts_committed_at`，导入在事实事务内写入；闸门按它取写入批次，历史期次校验全部候选（D4，Requirement「数据按月归档就绪」Scenario「同一期次多次导入以实际写入批次为准」「收尾失败的写入批次仍是写入者」「历史期次归属无法确认」）
- [x] 6.2 真实库回归：编号乱序、收尾失败、只校验确认的写入者、历史多候选、导入侧写入/回滚，`uv run pytest test/test_retention_archive_gate_integration.py test/test_import_service_integration.py -q`
