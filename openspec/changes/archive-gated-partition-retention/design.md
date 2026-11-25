## Context

- `run_retention` 按 `HOT_WINDOW_MONTHS` 枚举 `PARTITIONED_TABLES`（`fact_daily`、`fact_weekly`、`fact_dropped`、`fact_growth`）的超期月分区并 DROP，写路径持与导入共用的咨询锁，拿不到则 `lock_skipped`。
- 生产（2026-11-09 只读核对）：`www` crontab、`/etc/cron.*`、systemd timer、ops_task 历史都没有保留清理；root crontab 无权读取，发布前需复核。`HOT_WINDOW_MONTHS=3`。
- OSS `keywordiq-release` 共 196 个对象 35.82G，生命周期只有「7 天转 IA」与「7 天清未完成分片」，没有过期规则。
- `import_batch` 记录 `site / period_type(DAY|WEEK) / report_date / status / written_rows / object_key`，没有存文件大小或校验和。
- `ops_bot` 是宿主机独立环境，不得 import 业务 `app`；`alembic_drift` 已有 `docker compose run --rm app ...` 的先例。
- 调度器 `_run_once` 遇异常标记 FAILED，而 `daily_at` 判据只看最近一次 SUCCEEDED，因此失败任务会每 60 秒重跑一次。

## Goals / Non-Goals

**Goals:**

- 没有可验证的原始归档，就绝不删除该月数据（fail closed）。
- 每日自动运行，有留痕、有告警，真实删除需要显式开启。
- 12-01 前可上线，并有 dry-run 观察期。

**Non-Goals:**

- 不生成 Parquet 或训练数据集：原始 CSV 带完整文本，可重放；内部 ID 导出离开维度表无法独立使用。
- 不校验 CSV 内容与库内数据逐行一致（批次没有存校验和）。
- 不修改 OSS 生命周期；90 天转归档存储另行决定（见 Open Questions）。
- 不处理 V1 残留和扩盘。

## Decisions

### D1：闸门放在 `run_retention`，无绕过参数

CLI、定时任务都走同一入口。备选是只在 ops job 里预检查，但手工 `--yes` 就能绕开；对不可逆操作不接受。校验器以参数注入（默认用 `oss_client.stat_object`），便于测试。

### D2：按自然月整体判定

一个月任一期次不通过，该月四张表全部保留。备选是按表判定，但会出现事实表还在而掉榜/增长榜被删的不一致，违反「掉榜记录随事实数据一同」的现有要求。

只校验 `fact_daily`、`fact_weekly` 的期次：掉榜与增长榜是导入时由它们算出的派生数据，没有独立原始文件。

### D3：用「日历日 × 站点叶子分区 EXISTS」枚举期次

对每个站点、月内每一天执行 `SELECT EXISTS(... WHERE site=:s AND report_date=:d)`，命中 `(site, report_date, ...)` 复合索引，每月每表不超过 31×站点数次点查。备选 `SELECT DISTINCT` 需扫描千万级行。站点集合来自该月分区下的实际子分区，而不是 `ENABLED_SITES`，避免漏掉已停用站点。

**连接池/内存**：单连接串行点查，无大结果集，占用一个同步连接。**执行计划**：2026-11-09 在生产只读 `EXPLAIN`，日报、周报点查均为 Index Only Scan，批次查询为小表 Seq Scan（估算约 62 行），不新增索引。

### D4：每期次以事实的实际写入批次为准

同一 `(site, period, date)` 可能多次导入，重导会先清后写，库内数据属于最后一次**提交事实**的批次。原设计校验 `id` 最大的 COMPLETED 批次，#209 复核指出它不成立：`id` 在上传时分配，排队、重试后提交顺序可以相反；收尾中断的批次被 `retry_pending_query_stats` 补完时，可能晚于真正覆盖数据的批次才标成 COMPLETED；收尾失败的批次状态为 FAILED，事实却在库里。

改为在事实事务内写 `import_batch.facts_committed_at`（数据库 `clock_timestamp()`，导入串行锁内写入，同期次先后即提交先后；事务回滚时一并回滚）。闸门取同期次该列最新的批次，只校验它。

迁移前批次不回填：无从确认写入者，猜一个值会把不确定伪装成确定。没有提交记录的期次改为校验全部可能的写入者——COMPLETED 批次，以及 `written_rows > 0` 的任何批次（2026-10-24 起计数与事实同一事务提交）。真正的写入者必在其中，全部通过才放行；多个候选中任一不通过，原因记为 `owner_unconfirmed:<原因>`。2026-10-24 之前收尾失败的批次计数为 0、无法识别，2026-11-25 生产只读核对：全部 6 个失败批次都死在事实事务内或上传阶段，没有这类批次。

任一失败原因都记录，并带上未通过的批次号：`no_batch`、`empty_object_key`、`no_rows`、`object_missing`、`object_empty`、`oss_unavailable`、`owner_unconfirmed:*`。

`stat_object` 的非 404 异常按 `oss_unavailable` 处理，该月保留。`OSS_ENABLED=false` 时存在超期月份则全部判为 `oss_unavailable`。

### D5：闸门在咨询锁内完成，全部校验先于第一条 DROP

校验放锁内，防止校验后重导改变批次。先判定所有超期月，再统一 DROP；最坏约百次 HEAD，只延迟导入启动数十秒，导入可接受。dry-run 同样跑闸门（只读），不取锁。

### D6：残留统计清理保持无条件

被拦截月份的 `site_data_stats` 等行仍按 cutoff 清理：它们是可重算的派生统计，且该月已在查询窗口之外。保持现有要求不变，避免为极少发生的路径增加分支。

### D7：ops job 通过 `docker compose run` 调用脚本并解析 JSON

新增 `--json`，stdout 最后一行输出结果。ops_bot 不 import 业务代码，且脚本版本与 `app` 镜像一致。子进程在 `asyncio.to_thread` 中执行，超时 30 分钟。

### D8：任务失败不走调度器的 FAILED 重试

job 捕获子进程失败、超时、解析失败，发告警后**正常返回**并记录失败摘要，使当日标记为已运行。否则删除任务会每 60 秒重跑一次、反复占用导入锁。代价是失败当天不重试，由次日运行补偿，幂等清理允许这样做。

### D9：`OPS_RETENTION_MODE` 三态

默认 `off`（不注册任务）；`dry_run` 只预览和告警；`execute` 先 dry-run 留痕，再执行。在任务首次真实运行前必须人工确认配置。每日北京时间 03:30 运行，使用 `daily_at`。

告警键 `retention-gate-blocked`（warn）和 `retention-job-failed`（crit），消息最多列 20 条期次明细，避免超长。

### D10：`--export-dir` 改用 `COPY (SELECT ...)`

月分区本身是带子分区的分区表，集成测试已复现 `cannot copy from partitioned table`，改为 `COPY (SELECT ...)`。定时任务不使用导出。

## Risks / Trade-offs

- [OSS 对象存在但内容损坏] → 批次无校验和，闸门只能证明存在且非空；对象写入后不改动，接受此风险。
- [有人日后给 bucket 加过期规则] → 闸门只保证删除当时的归档存在；在 runbook 和 spec 标明原始对象不得过期。
- [长事务持 DROP 锁影响在线查询] → 沿用现状，低峰运行；单月超期时分区数少。
- [某期次长期缺归档，分区一直不删] → 这是预期的安全行为，告警持续提示，由人工补传或决定。
- [生产 root crontab 已有清理任务] → 发布前复核；若存在需先移除，否则会绕过 job 留痕（闸门仍生效）。
- [磁盘] → 只保留三个月时高峰仍需 75～103G（V1 已删），建议扩至 130～150G，独立处理。

## Migration Plan

1. 合并并经 `Deploy V2 to ECS` 发布（无迁移），`OPS_RETENTION_MODE` 仍为 `off`。
2. 重启 ops-bot 宿主机服务使新代码生效（按现有 ops-bot 发布方式）。
3. 配置 `dry_run`，观察至少 2 天日志与告警；月内未超期时用 `--retention-months 2` 的 dry-run 验证闸门能覆盖 9 月。
4. 2026-12-01 前切换 `execute`，首次执行需用户确认。
5. 回滚：改回 `off` 并重启 ops-bot。已 DROP 的分区只能从 OSS 原始文件重导恢复。

## Open Questions

- 是否为 OSS 增加 90 天转归档存储：归档对象重导前需解冻，建议等 3 个月后按实际账单决定。
