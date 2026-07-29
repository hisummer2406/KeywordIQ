## Context

事实表 `fact_daily` / `fact_weekly` 采用两级分区：一级按 `report_date` 月度 RANGE、二级按 `site`
LIST（见 `app/table/analysis/partitioning.py`）。原设计以 12 个月为满窗、把"按月移出"作为归档能力
交付，但移出由运维手动执行。本变更把策略收敛为"只保留最近三个月"，并交由系统自动完成。

月度 RANGE 分区是这一策略成立的地基：移出一个月 = `DROP`（或 `DETACH` 后 `DROP`）该月的一级分区，
其站点子分区随父分区一并消失，O(1) 元数据操作，不触发逐行删除、不产生死元组、不影响其他月份。

约束：清理是写操作，须遵循分层约定（service 层控制事务，crud/partitioning 层不 commit）；不得在
FastAPI 事件循环上跑阻塞式 DDL；调度入口需可独立于 API 进程运行。

## Goals / Non-Goals

**Goals:**
- 日报与周报统一保留最近三个自然月，超期月度分区自动整体移出。
- 复用现有月度分区结构，移出为整分区 DDL，不逐行删除。
- 移出前可完整导出被移出月份的数据。
- 保留月数可配置（默认 3），清理幂等、可重复触发。

**Non-Goals:**
- 不改变分区的创建逻辑与命名规则（继续复用 `ensure_fact_partitions`）。
- 不实现冷/温数据分层或外部对象存储归档——超期即移出，本变更不负责长期留存介质。
- 不改变查询层：查询天然只覆盖存在的分区，无需感知保留窗口。
- 不做按天保留；窗口边界始终对齐自然月。

## Decisions

**决策 1：以自然月分区为移出单位，保留边界 = 当前自然月往前推 (retention_months - 1) 个月的月初。**
- 默认 3 个月即"当前月 + 前两个月"。落在该边界之前的月度分区判定为超期。
- 备选：按精确 90 天滚动窗口。否决——与既有月度分区粒度不匹配，会出现"半个分区超期"的无法整分区移出的情况。

**决策 2：移出用 `DROP TABLE IF EXISTS <月度分区>`（可选先 `DETACH`）。**
- 直接 DROP 一级月度分区即连带其站点子分区，最简。若需"移出前导出"，先查询/导出再 DROP。
- 备选：`DELETE ... WHERE report_date < 边界`。否决——逐行删除违反数据模型约束（事实表只追加、按月归档是死元组归零的前提），且慢、产生膨胀。

**决策 3：清理能力落在 `partitioning.py`，调度入口独立于 API 进程。**
- 在 `partitioning.py` 新增 `drop_partitions_older_than(session, table_name, cutoff)` 之类的纯移出函数（不 commit），
  由 service 层封装事务与"两张事实表 + 导出"编排。
- 调度入口做成可 `uv run python -m ...` 触发的脚本（放 `scripts/`），交由 cron / 容器定时任务调用；
  不放在事件循环、不新增常驻调度依赖。
- 备选：进程内 APScheduler。否决——多 worker 下会重复触发，且给读多写少的 API 进程徒增复杂度。

**决策 4：复用既有配置 `HOT_WINDOW_MONTHS`（默认由 12 改为 3），不新增配置项。**
- 实施时发现 `config.py` 已有 `HOT_WINDOW_MONTHS`（语义即"库中保留多少个月、更早的移出"），
  与本变更需求完全一致。复用它、把默认改为 3，避免出现两个含义相同的配置项。
- 同步更新 `.env.example` 的默认值与注释。清理逻辑读取 `settings.HOT_WINDOW_MONTHS` 计算 cutoff，
  脚本 `--retention-months` 可临时覆盖。
- （原计划新增 `DATA_RETENTION_MONTHS`，因发现既有配置而调整为复用。）

**决策 5：识别待移出分区用 PostgreSQL 目录而非字符串猜名。**
- 通过 `pg_inherits` / `pg_class` 枚举父表的现有一级分区及其 RANGE 边界，与 cutoff 比较，
  只移出确实超期者。避免因命名假设误删。

## Risks / Trade-offs

- [误删仍需查询的数据] → 保留月数可配置且默认保守（3）；移出前提供完整导出；清理脚本先 dry-run 打印将移出的分区名，确认后再执行。
- [DROP 持有父表 ACCESS EXCLUSIVE 锁，可能与在线查询/导入争锁] → 移出安排在低峰调度；优先 `DETACH CONCURRENTLY`（PG14+）再 DROP，缩短持锁时间；单次只处理少量超期分区。
- [首次上线时存量超期数据一次性移出量大] → 上线迁移中分批移出并逐月导出留档，而非一次性 DROP 全部历史。
- [调度未运行导致数据超窗堆积] → 清理幂等，补跑即可追平；容量按"窗口 + 少量宽限"预留，短暂超窗不致爆盘。
- [容量文档失真] → 本变更同步重写 `CLAUDE.md` 的容量与扩容段落（满窗约 2 亿行 / 50 GB，不再需要扩到 8C32G）。

## Migration Plan

1. 新增 `DATA_RETENTION_MONTHS` 配置与 `.env.example` 默认值。
2. 在 `partitioning.py` 增加枚举 + 移出超期月度分区的函数；service 层封装事务与导出编排。
3. 增加 `scripts/` 下可定时触发的清理入口，支持 `--dry-run`。
4. 存量迁移：对已有历史，逐月导出留档后移出超过三个月的分区（分批）。
5. 配置定时任务（cron / 容器）按日触发清理。
6. 重写 `CLAUDE.md` 容量规划与扩容触发条件段落。
7. 回滚：停用定时任务即停止继续移出；已 DROP 的分区不可回滚，故上线阶段依赖导出留档。

## Open Questions

- 被移出月份的导出留档去向（本地卷 / 对象存储 / 仅供人工导出）是否需要在本变更内固化，还是交由运维流程？
- `DETACH CONCURRENTLY` 在生产 PG15 下与导入并发时的实际锁行为是否需压测确认。
