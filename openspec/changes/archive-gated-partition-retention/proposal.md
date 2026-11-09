## Why

保留清理（`scripts/prune_retention.py`）已实现，但生产环境没有任何调度在调用它：超期分区不会被移出。2026-12-01 起 9 月分区（约 10G）开始超窗，系统盘按 13～20G/月增长，约 1.4～2.2 个月写满。

直接开启自动清理也不可接受：分区 DROP 不可恢复，生产库没有备份，而这些数据要为后续 AI 训练长期积累。OSS 上的原始 CSV 已是事实上的归档（无过期规则，2026-11-09 核对生产 165 个日报期次与 27 个周报期次均有带 object key 的成功批次），但清理逻辑目前并不校验归档是否真的存在。

## What Changes

- 保留清理在移出某月前 SHALL 校验该月**日报与周报两条路径**每个站点、每个期次的原始归档：存在成功批次、object key 非空、写入行数大于 0、OSS 对象存在且大小大于 0。
- 校验按**自然月整体**判定，任一期次不通过，该月所有分区表（日报、周报、掉榜、增长榜）全部保留。校验无法完成（OSS 不可用、未启用）时同样不删除。
- 该闸门位于 `run_retention` 内，CLI 与定时任务都经过它，不提供绕过参数。
- 运维机器人新增每日定时任务，通过 `app` 镜像运行清理脚本，先 dry-run 留痕再执行；闸门拦截或任务失败时发飞书告警。
- 新增配置 `OPS_RETENTION_MODE`（`off` / `dry_run` / `execute`），默认 `off`，真实删除必须显式开启。
- 修复 `--export-dir` 对带站点子分区的月分区的导出路径（先用真实库确认）。
- OSS 原始对象永不由清理流程删除或移动。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `multi-site-data-model`：「数据按月归档就绪」增加归档闸门，超期月份只有归档校验通过才移出。
- `ops-monitoring-alerting`：新增定时保留清理任务及其告警。

## Impact

- **影响路径**：日报与周报两者；掉榜、增长榜随所在月份一同受闸门约束。导入路径不变。
- **代码**：`app/table/analysis/retention_service.py`、`app/table/upload/oss_client.py`（复用）、`scripts/prune_retention.py`、`ops_bot/jobs.py`、新增 `ops_bot/checks/` 或同级任务模块、`config.py`、`.env.example`。
- **数据库**：无 schema 变更，无 Alembic 迁移。
- **外部依赖**：OSS `head_object`，每月每站点至多约 37 次（31 个日报期次 + 约 5 个周报期次）。
- **生产**：发布后需显式配置 `OPS_RETENTION_MODE`；需在 2026-12-01 前完成发布与 dry-run 观察。
- **容量**：不替代扩盘，磁盘规划见 design。
